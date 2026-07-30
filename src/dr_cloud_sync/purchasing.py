"""Supplier V1 use cases and durable SQLite adapter."""
from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import sqlite3
from pathlib import Path
from typing import Protocol
import uuid

from .domain import (ActivityLog, GoodsReceipt, GoodsReceiptLine, GoodsReceiptStatus,
                     MovementStatus, MovementType, ProductStatus, PurchaseOrder,
                     PurchaseOrderLine, PurchaseOrderStatus, StockMovement, Supplier,
                     SupplierStatus, utc_now)
from .services import StockService


class SupplierRepository(Protocol):
    def create(self, supplier: Supplier) -> Supplier: ...
    def get(self, supplier_id: str) -> Supplier | None: ...
    def list(self, status: SupplierStatus | None = None) -> list[Supplier]: ...
    def update(self, supplier: Supplier) -> Supplier: ...
    def transition_status(self, supplier_id: str, status: SupplierStatus) -> Supplier: ...
    def search(self, query: str, status: SupplierStatus | None = None) -> list[Supplier]: ...


class DuplicateSupplierIdentity(ValueError):
    pass


class SQLiteSupplierRepository:
    COLUMNS = ("supplier_id","name","status","email","phone","website","address",
               "postal_code","city","country","contact_name","notes","created_at","updated_at")

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS suppliers(
          supplier_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
          email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
          country TEXT NOT NULL DEFAULT '', contact_name TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_suppliers_status ON suppliers(status)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_suppliers_name ON suppliers(name)")
        self.db.commit()

    @classmethod
    def _supplier(cls, row):
        return Supplier(**{key: row[key] for key in cls.COLUMNS}) if row else None

    def create(self, supplier):
        try:
            with self.db:
                self.db.execute(f"INSERT INTO suppliers({','.join(self.COLUMNS)}) VALUES({','.join('?'*len(self.COLUMNS))})",
                                tuple(getattr(supplier, key).value if key == "status" else getattr(supplier, key) for key in self.COLUMNS))
        except sqlite3.IntegrityError as exc:
            raise DuplicateSupplierIdentity("supplier_id already exists") from exc
        return supplier

    def get(self, supplier_id):
        return self._supplier(self.db.execute("SELECT * FROM suppliers WHERE supplier_id=?",(supplier_id,)).fetchone())

    def list(self, status=None):
        sql="SELECT * FROM suppliers"; params=()
        if status: sql+=" WHERE status=?"; params=(SupplierStatus(status).value,)
        return [self._supplier(row) for row in self.db.execute(sql+" ORDER BY name COLLATE NOCASE,supplier_id",params)]

    def update(self, supplier):
        fields=self.COLUMNS[1:]
        with self.db:
            result=self.db.execute(f"UPDATE suppliers SET {','.join(f'{x}=?' for x in fields)} WHERE supplier_id=?",
              tuple(getattr(supplier,x).value if x=="status" else getattr(supplier,x) for x in fields)+(supplier.supplier_id,))
        if not result.rowcount: raise KeyError("supplier not found")
        return supplier

    def transition_status(self, supplier_id, status):
        supplier=self.get(supplier_id)
        if not supplier: raise KeyError("supplier not found")
        supplier.transition_to(status); return self.update(supplier)

    def search(self, query, status=None):
        term=f"%{query.strip()}%"; where="(name LIKE ? OR contact_name LIKE ? OR email LIKE ? OR phone LIKE ?)"
        params=(term,)*4
        if status: where+=" AND status=?"; params+=(SupplierStatus(status).value,)
        return [self._supplier(row) for row in self.db.execute(f"SELECT * FROM suppliers WHERE {where} ORDER BY name COLLATE NOCASE",params)]


class SupplierService:
    LIMITS={"name":200,"email":254,"phone":50,"website":500,"address":500,"postal_code":30,
            "city":120,"country":120,"contact_name":200,"notes":5000}
    EMAIL=re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

    def __init__(self, repository: SupplierRepository, audit):
        self.repository=repository; self.audit=audit

    def _values(self, data, current=None):
        values={}
        for key, limit in self.LIMITS.items():
            value=data.get(key, getattr(current,key,"") if current else "")
            if not isinstance(value,str): raise ValueError(f"{key} must be text")
            value=value.strip()
            if len(value)>limit: raise ValueError(f"{key} is too long")
            values[key]=value
        if not values["name"]: raise ValueError("name is required")
        if values["email"] and not self.EMAIL.fullmatch(values["email"]): raise ValueError("email is invalid")
        return values

    def duplicates(self, name, exclude=None):
        normalized=" ".join(name.casefold().split())
        return [s for s in self.repository.list() if s.supplier_id != exclude and " ".join(s.name.casefold().split()) == normalized]

    def create(self, data, actor="authenticated"):
        values=self._values(data); supplier=Supplier(supplier_id=f"sup:{uuid.uuid4()}",**values)
        self.repository.create(supplier)
        self.audit.add_activity(ActivityLog("SUPPLIER_CREATED",supplier.supplier_id,"PURCHASING",{"actor":actor}))
        return supplier, self.duplicates(supplier.name,supplier.supplier_id)

    def get(self, supplier_id): return self.repository.get(supplier_id)
    def list(self, query="", status=None): return self.repository.search(query,status) if query else self.repository.list(status)

    def update(self, supplier_id, data, actor="authenticated"):
        current=self.repository.get(supplier_id)
        if not current: raise KeyError("supplier not found")
        if "supplier_id" in data and data["supplier_id"] != supplier_id: raise ValueError("supplier_id is immutable")
        if "status" in data: raise ValueError("use the status transition endpoint")
        changed=replace(current,**self._values(data,current),updated_at=utc_now())
        self.repository.update(changed)
        self.audit.add_activity(ActivityLog("SUPPLIER_UPDATED",supplier_id,"PURCHASING",{"actor":actor}))
        return changed, self.duplicates(changed.name,supplier_id)

    def transition(self, supplier_id, status, actor="authenticated"):
        before=self.repository.get(supplier_id)
        if not before: raise KeyError("supplier not found")
        changed=self.repository.transition_status(supplier_id,SupplierStatus(status))
        if changed.status != before.status:
            self.audit.add_activity(ActivityLog("SUPPLIER_STATUS_CHANGED",supplier_id,"PURCHASING",{"actor":actor,"from":before.status.value,"to":changed.status.value}))
        return changed

    def activities(self, supplier_id):
        return [a for a in self.audit.activities() if a.drcloud_product_key == supplier_id]


class PurchaseOrderRepository(Protocol):
    def create(self, order: PurchaseOrder) -> PurchaseOrder: ...
    def get(self, order_id: str) -> PurchaseOrder | None: ...
    def list(self, status: PurchaseOrderStatus | None = None) -> list[PurchaseOrder]: ...
    def update_draft(self, order: PurchaseOrder) -> PurchaseOrder: ...
    def transition_status(self, order_id: str, status: PurchaseOrderStatus, ordered_at: str | None) -> PurchaseOrder: ...
    def add_line(self, line: PurchaseOrderLine) -> PurchaseOrderLine: ...
    def update_line(self, line: PurchaseOrderLine) -> PurchaseOrderLine: ...
    def remove_line(self, order_id: str, line_id: str) -> None: ...
    def list_lines(self, order_id: str) -> list[PurchaseOrderLine]: ...


class SQLitePurchaseOrderRepository:
    ORDER_COLUMNS=("purchase_order_id","supplier_id","status","reference","supplier_reference","ordered_at","expected_at","notes","currency","created_at","updated_at")
    LINE_COLUMNS=("line_id","purchase_order_id","product_key","supplier_product_reference","ordered_quantity","unit_cost","created_at","updated_at")
    def __init__(self,path:Path):
        self.db=sqlite3.connect(path,check_same_thread=False); self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""CREATE TABLE IF NOT EXISTS purchase_orders(
          purchase_order_id TEXT PRIMARY KEY,supplier_id TEXT NOT NULL,status TEXT NOT NULL,reference TEXT NOT NULL UNIQUE,
          supplier_reference TEXT NOT NULL DEFAULT '',ordered_at TEXT,expected_at TEXT,notes TEXT NOT NULL DEFAULT '',
          currency TEXT NOT NULL DEFAULT 'EUR',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
          FOREIGN KEY(supplier_id) REFERENCES suppliers(supplier_id))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS purchase_order_lines(
          line_id TEXT PRIMARY KEY,purchase_order_id TEXT NOT NULL,product_key TEXT NOT NULL,
          supplier_product_reference TEXT NOT NULL DEFAULT '',ordered_quantity INTEGER NOT NULL CHECK(ordered_quantity>0),
          unit_cost TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
          FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(purchase_order_id) ON DELETE RESTRICT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_purchase_orders_status ON purchase_orders(status)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_purchase_orders_supplier ON purchase_orders(supplier_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_purchase_order_lines_order ON purchase_order_lines(purchase_order_id)"); self.db.commit()
    def _order(self,r): return PurchaseOrder(**{k:r[k] for k in self.ORDER_COLUMNS}) if r else None
    def _line(self,r): return PurchaseOrderLine(**{k:r[k] for k in self.LINE_COLUMNS}) if r else None
    def create(self,o):
        with self.db:self.db.execute(f"INSERT INTO purchase_orders({','.join(self.ORDER_COLUMNS)}) VALUES({','.join('?'*len(self.ORDER_COLUMNS))})",tuple(getattr(o,k).value if k=='status' else getattr(o,k) for k in self.ORDER_COLUMNS))
        return o
    def get(self,i): return self._order(self.db.execute("SELECT * FROM purchase_orders WHERE purchase_order_id=?",(i,)).fetchone())
    def list(self,status=None):
        where=" WHERE status=?" if status else ""; params=(PurchaseOrderStatus(status).value,) if status else ()
        return [self._order(r) for r in self.db.execute("SELECT * FROM purchase_orders"+where+" ORDER BY created_at DESC",params)]
    def update_draft(self,o):
        with self.db:r=self.db.execute("""UPDATE purchase_orders SET supplier_id=?,reference=?,supplier_reference=?,expected_at=?,notes=?,currency=?,updated_at=? WHERE purchase_order_id=? AND status='DRAFT'""",(o.supplier_id,o.reference,o.supplier_reference,o.expected_at,o.notes,o.currency,o.updated_at,o.purchase_order_id))
        if not r.rowcount: raise ValueError("only DRAFT purchase orders can be modified")
        return o
    def transition_status(self,i,status,ordered_at):
        now=utc_now()
        with self.db:r=self.db.execute("UPDATE purchase_orders SET status=?,ordered_at=COALESCE(?,ordered_at),updated_at=? WHERE purchase_order_id=?",(status.value,ordered_at,now,i))
        if not r.rowcount: raise KeyError("purchase order not found")
        return self.get(i)
    def add_line(self,line):
        with self.db:self.db.execute(f"INSERT INTO purchase_order_lines({','.join(self.LINE_COLUMNS)}) VALUES({','.join('?'*len(self.LINE_COLUMNS))})",tuple(getattr(line,k) for k in self.LINE_COLUMNS))
        return line
    def update_line(self,line):
        with self.db:r=self.db.execute("UPDATE purchase_order_lines SET supplier_product_reference=?,ordered_quantity=?,unit_cost=?,updated_at=? WHERE line_id=? AND purchase_order_id=?",(line.supplier_product_reference,line.ordered_quantity,line.unit_cost,line.updated_at,line.line_id,line.purchase_order_id))
        if not r.rowcount: raise KeyError("purchase order line not found")
        return line
    def remove_line(self,oid,lid):
        with self.db:r=self.db.execute("DELETE FROM purchase_order_lines WHERE purchase_order_id=? AND line_id=?",(oid,lid))
        if not r.rowcount: raise KeyError("purchase order line not found")
    def list_lines(self,oid): return [self._line(r) for r in self.db.execute("SELECT * FROM purchase_order_lines WHERE purchase_order_id=? ORDER BY created_at,line_id",(oid,))]


class PurchaseOrderService:
    def __init__(self,repository,suppliers,catalogue,audit): self.repository=repository;self.suppliers=suppliers;self.catalogue=catalogue;self.audit=audit
    def _audit(self,event,oid,actor,**metadata): self.audit.add_activity(ActivityLog(event,oid,"PURCHASING",{"actor":actor,**metadata}))
    def _supplier(self,sid):
        supplier=self.suppliers.get(sid)
        if not supplier: raise ValueError("supplier not found")
        if supplier.status is not SupplierStatus.ACTIVE: raise ValueError("supplier must be ACTIVE")
    def _cost(self,value):
        if value in (None,""): return None
        try: amount=Decimal(str(value))
        except (InvalidOperation,ValueError): raise ValueError("unit_cost is invalid")
        if not amount.is_finite() or amount < 0: raise ValueError("unit_cost must be positive or zero")
        return str(amount.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))
    def create(self,data,actor="authenticated"):
        self._supplier(data.get("supplier_id","")); now=utc_now(); oid=f"po:{uuid.uuid4()}"
        order=PurchaseOrder(oid,data["supplier_id"],data.get("reference","").strip() or f"PO-{now[:10].replace('-','')}-{oid[-6:].upper()}",supplier_reference=str(data.get("supplier_reference") or "").strip(),expected_at=data.get("expected_at") or None,notes=str(data.get("notes") or "").strip())
        self.repository.create(order); self._audit("PURCHASE_ORDER_CREATED",oid,actor); return order
    def get(self,i): return self.repository.get(i)
    def list(self,status=None): return self.repository.list(PurchaseOrderStatus(status) if status else None)
    def update(self,i,data,actor="authenticated"):
        old=self.repository.get(i)
        if not old: raise KeyError("purchase order not found")
        if "purchase_order_id" in data and data["purchase_order_id"]!=i: raise ValueError("purchase_order_id is immutable")
        if "status" in data: raise ValueError("use the status transition endpoint")
        sid=data.get("supplier_id",old.supplier_id); self._supplier(sid)
        changed=replace(old,supplier_id=sid,reference=str(data.get("reference",old.reference)).strip(),supplier_reference=str(data.get("supplier_reference",old.supplier_reference)).strip(),expected_at=data.get("expected_at",old.expected_at) or None,notes=str(data.get("notes",old.notes)).strip(),updated_at=utc_now())
        self.repository.update_draft(changed); self._audit("PURCHASE_ORDER_UPDATED",i,actor); return changed
    def _editable(self,i):
        o=self.repository.get(i)
        if not o: raise KeyError("purchase order not found")
        if o.status is not PurchaseOrderStatus.DRAFT: raise ValueError("only DRAFT purchase orders can be modified")
        return o
    def _product(self,key):
        p=self.catalogue.get(key)
        if not p: raise ValueError("product not found")
        if p.status is not ProductStatus.ACTIVE: raise ValueError("product must be ACTIVE")
    def add_line(self,i,data,actor="authenticated"):
        self._editable(i); key=str(data.get("product_key") or ""); self._product(key)
        try:q=int(data.get("ordered_quantity"))
        except (TypeError,ValueError): raise ValueError("ordered_quantity must be a positive integer")
        if isinstance(data.get("ordered_quantity"), bool) or str(data.get("ordered_quantity")).strip()!=str(q): raise ValueError("ordered_quantity must be a positive integer")
        line=PurchaseOrderLine(f"pol:{uuid.uuid4()}",i,key,q,str(data.get("supplier_product_reference") or "").strip(),self._cost(data.get("unit_cost")))
        self.repository.add_line(line); self._audit("PURCHASE_ORDER_LINE_ADDED",i,actor,line_id=line.line_id); return line
    def update_line(self,i,lid,data,actor="authenticated"):
        self._editable(i); old=next((x for x in self.repository.list_lines(i) if x.line_id==lid),None)
        if not old: raise KeyError("purchase order line not found")
        raw_quantity=data.get("ordered_quantity",old.ordered_quantity)
        try:q=int(raw_quantity)
        except (TypeError,ValueError): raise ValueError("ordered_quantity must be a positive integer")
        if isinstance(raw_quantity,bool) or str(raw_quantity).strip()!=str(q): raise ValueError("ordered_quantity must be a positive integer")
        line=replace(old,ordered_quantity=q,supplier_product_reference=str(data.get("supplier_product_reference",old.supplier_product_reference)).strip(),unit_cost=self._cost(data.get("unit_cost",old.unit_cost)),updated_at=utc_now())
        self.repository.update_line(line); self._audit("PURCHASE_ORDER_LINE_UPDATED",i,actor,line_id=lid); return line
    def remove_line(self,i,lid,actor="authenticated"):
        self._editable(i); self.repository.remove_line(i,lid); self._audit("PURCHASE_ORDER_LINE_REMOVED",i,actor,line_id=lid)
    def lines(self,i): return self.repository.list_lines(i)
    def transition(self,i,target,actor="authenticated"):
        order=self.repository.get(i)
        if not order: raise KeyError("purchase order not found")
        target=PurchaseOrderStatus(target)
        if target==order.status: return order
        allowed={PurchaseOrderStatus.DRAFT:{PurchaseOrderStatus.ORDERED,PurchaseOrderStatus.CANCELLED},PurchaseOrderStatus.ORDERED:{PurchaseOrderStatus.CANCELLED}}
        if target not in allowed.get(order.status,set()): raise ValueError(f"transition {order.status} -> {target} is forbidden")
        if target is PurchaseOrderStatus.ORDERED:
            self._supplier(order.supplier_id); lines=self.lines(i)
            if not lines: raise ValueError("an empty purchase order cannot be ordered")
            for line in lines:self._product(line.product_key)
        changed=self.repository.transition_status(i,target,utc_now() if target is PurchaseOrderStatus.ORDERED else None)
        self._audit("PURCHASE_ORDER_ORDERED" if target is PurchaseOrderStatus.ORDERED else "PURCHASE_ORDER_CANCELLED",i,actor)
        return changed
    def total(self,i):
        lines=self.lines(i)
        if any(x.unit_cost is None for x in lines): return None
        return str(sum((Decimal(x.unit_cost)*x.ordered_quantity for x in lines),Decimal()).quantize(Decimal("0.01")))
    def activities(self,i): return [a for a in self.audit.activities() if a.drcloud_product_key==i]


class SQLiteGoodsReceiptRepository:
    """Additive SQLite persistence for immutable, applied supplier receipts."""
    RECEIPT_COLUMNS=("receipt_id","purchase_order_id","status","received_at","received_by","notes","created_at","applied_at","idempotency_key")
    LINE_COLUMNS=("receipt_line_id","receipt_id","purchase_order_line_id","product_key","received_quantity")
    def __init__(self,path:Path,db=None):
        self.db=db or sqlite3.connect(path,check_same_thread=False); self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""CREATE TABLE IF NOT EXISTS goods_receipts(
          receipt_id TEXT PRIMARY KEY,purchase_order_id TEXT NOT NULL,status TEXT NOT NULL,
          received_at TEXT NOT NULL,received_by TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,applied_at TEXT,idempotency_key TEXT NOT NULL UNIQUE,
          FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(purchase_order_id) ON DELETE RESTRICT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS goods_receipt_lines(
          receipt_line_id TEXT PRIMARY KEY,receipt_id TEXT NOT NULL,purchase_order_line_id TEXT NOT NULL,
          product_key TEXT NOT NULL,received_quantity INTEGER NOT NULL CHECK(received_quantity>0),
          FOREIGN KEY(receipt_id) REFERENCES goods_receipts(receipt_id) ON DELETE RESTRICT,
          FOREIGN KEY(purchase_order_line_id) REFERENCES purchase_order_lines(line_id) ON DELETE RESTRICT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_goods_receipts_order ON goods_receipts(purchase_order_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_goods_receipt_lines_receipt ON goods_receipt_lines(receipt_id)"); self.db.commit()
    def _receipt(self,r): return GoodsReceipt(**{k:r[k] for k in self.RECEIPT_COLUMNS}) if r else None
    def _line(self,r): return GoodsReceiptLine(**{k:r[k] for k in self.LINE_COLUMNS}) if r else None
    def get(self,i): return self._receipt(self.db.execute("SELECT * FROM goods_receipts WHERE receipt_id=?",(i,)).fetchone())
    def by_key(self,key): return self._receipt(self.db.execute("SELECT * FROM goods_receipts WHERE idempotency_key=?",(key,)).fetchone())
    def list(self,order_id=None):
        sql="SELECT * FROM goods_receipts"; params=()
        if order_id: sql+=" WHERE purchase_order_id=?"; params=(order_id,)
        return [self._receipt(r) for r in self.db.execute(sql+" ORDER BY created_at DESC",params)]
    def lines(self,i): return [self._line(r) for r in self.db.execute("SELECT * FROM goods_receipt_lines WHERE receipt_id=? ORDER BY receipt_line_id",(i,))]
    def received(self,order_id):
        return {r[0]:r[1] for r in self.db.execute("""SELECT l.purchase_order_line_id,COALESCE(SUM(l.received_quantity),0)
          FROM goods_receipt_lines l JOIN goods_receipts r ON r.receipt_id=l.receipt_id
          WHERE r.purchase_order_id=? AND r.status='APPLIED' GROUP BY l.purchase_order_line_id""",(order_id,))}


class GoodsReceiptService:
    """Explicit receipt workflow; archived suppliers/products remain receivable commitments."""
    def __init__(self,repository,orders,stock_repository,audit):
        self.repository=repository; self.orders=orders; self.stock=StockService(stock_repository); self.audit=audit
    def receivable(self,oid):
        order=self.orders.get(oid)
        if not order: raise KeyError("purchase order not found")
        received=self.repository.received(oid)
        return [{**line.__dict__,"received_quantity":received.get(line.line_id,0),"remaining_quantity":line.ordered_quantity-received.get(line.line_id,0)} for line in self.orders.lines(oid)]
    def create(self,oid,data,actor="authenticated"):
        order=self.orders.get(oid)
        if not order: raise KeyError("purchase order not found")
        if order.status not in (PurchaseOrderStatus.ORDERED,PurchaseOrderStatus.PARTIALLY_RECEIVED): raise ValueError("purchase order cannot be received")
        key=str(data.get("idempotency_key") or "").strip()
        if not key: raise ValueError("idempotency_key is required")
        existing=self.repository.by_key(key)
        if existing: return existing
        available={x["line_id"]:x for x in self.receivable(oid)}; requested=data.get("lines") or []
        if not requested: raise ValueError("at least one receipt line is required")
        rid=f"gr:{uuid.uuid4()}"; now=utc_now(); lines=[]; seen=set()
        for item in requested:
            lid=str(item.get("purchase_order_line_id") or "")
            if lid in seen or lid not in available: raise ValueError("purchase order line is invalid or duplicated")
            seen.add(lid)
            raw=item.get("received_quantity")
            try: quantity=int(raw)
            except (TypeError,ValueError): raise ValueError("received_quantity must be a positive integer")
            if isinstance(raw,bool) or str(raw).strip()!=str(quantity) or quantity<=0: raise ValueError("received_quantity must be a positive integer")
            if quantity>available[lid]["remaining_quantity"]: raise ValueError("received_quantity exceeds remaining quantity")
            lines.append(GoodsReceiptLine(f"grl:{uuid.uuid4()}",rid,lid,available[lid]["product_key"],quantity))
        receipt=GoodsReceipt(rid,oid,received_by=actor,notes=str(data.get("notes") or "").strip(),idempotency_key=key)
        with self.repository.db:
            self.repository.db.execute(f"INSERT INTO goods_receipts({','.join(self.repository.RECEIPT_COLUMNS)}) VALUES({','.join('?'*len(self.repository.RECEIPT_COLUMNS))})",tuple(getattr(receipt,k).value if k=='status' else getattr(receipt,k) for k in self.repository.RECEIPT_COLUMNS))
            for line in lines:self.repository.db.execute(f"INSERT INTO goods_receipt_lines({','.join(self.repository.LINE_COLUMNS)}) VALUES({','.join('?'*len(self.repository.LINE_COLUMNS))})",tuple(getattr(line,k) for k in self.repository.LINE_COLUMNS))
        self.audit.add_activity(ActivityLog("GOODS_RECEIPT_CREATED",rid,"PURCHASING",{"actor":actor,"purchase_order_id":oid}))
        return receipt
    def apply(self,rid,actor="authenticated"):
        receipt=self.repository.get(rid)
        if not receipt: raise KeyError("goods receipt not found")
        if receipt.status is GoodsReceiptStatus.APPLIED: return receipt
        order=self.orders.get(receipt.purchase_order_id)
        if not order or order.status not in (PurchaseOrderStatus.ORDERED,PurchaseOrderStatus.PARTIALLY_RECEIVED): raise ValueError("purchase order cannot be received")
        # Revalidate under SQLite's write lock. Stable movement keys make timeout/concurrent retries safe.
        db=self.repository.db; db.execute("BEGIN IMMEDIATE")
        try:
            current=self.repository.get(rid)
            if current.status is GoodsReceiptStatus.APPLIED: db.rollback(); return current
            available={x["line_id"]:x for x in self.receivable(order.purchase_order_id)}
            for line in self.repository.lines(rid):
                if line.received_quantity>available[line.purchase_order_line_id]["remaining_quantity"]: raise ValueError("received_quantity exceeds remaining quantity")
                movement_time=utc_now()
                self.stock.apply(StockMovement(line.product_key,line.received_quantity,MovementType.SUPPLIER_RECEIPT,"GOODS_RECEIPT",rid,f"{rid}:{line.receipt_line_id}",MovementStatus.APPLIED,validated_at=movement_time,applied_at=movement_time,actor=actor))
            now=utc_now(); db.execute("UPDATE goods_receipts SET status='APPLIED',applied_at=? WHERE receipt_id=? AND status='DRAFT'",(now,rid))
            remaining=sum(x["remaining_quantity"] for x in self.receivable(order.purchase_order_id))
            status=PurchaseOrderStatus.RECEIVED if remaining==0 else PurchaseOrderStatus.PARTIALLY_RECEIVED
            db.execute("UPDATE purchase_orders SET status=?,updated_at=? WHERE purchase_order_id=?",(status.value,now,order.purchase_order_id)); db.commit()
        except Exception: db.rollback(); raise
        self.audit.add_activity(ActivityLog("GOODS_RECEIPT_APPLIED",rid,"PURCHASING",{"actor":actor,"purchase_order_id":order.purchase_order_id}))
        self.audit.add_activity(ActivityLog("PURCHASE_ORDER_RECEIVED" if status is PurchaseOrderStatus.RECEIVED else "PURCHASE_ORDER_PARTIALLY_RECEIVED",order.purchase_order_id,"PURCHASING",{"actor":actor,"receipt_id":rid}))
        return self.repository.get(rid)
    def detail(self,rid):
        receipt=self.repository.get(rid)
        if not receipt: raise KeyError("goods receipt not found")
        return receipt,self.repository.lines(rid)
