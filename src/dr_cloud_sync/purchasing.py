"""Supplier V1 use cases and durable SQLite adapter."""
from __future__ import annotations

from dataclasses import asdict, replace
import re
import sqlite3
from pathlib import Path
from typing import Protocol
import uuid

from .domain import ActivityLog, Supplier, SupplierStatus, utc_now


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
