"""Evidence-only purchase costs and FIFO projections.

This module deliberately does not own purchasing, stock, sales or catalogue.  It
stores the links between those authorities and never substitutes a current price
for missing historical evidence.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import csv, hashlib, io, json, sqlite3, uuid
from .schema import ensure_schema

TWOPLACES=Decimal("0.01")
STATUSES={"CONFIRMED","ESTIMATED","INCOMPLETE","CONFLICT","CANCELLED"}
MAPPING_STATUSES={"MATCHED","UNMATCHED","AMBIGUOUS","CONFLICT"}

SCHEMA="""
CREATE TABLE IF NOT EXISTS supplier_product_mappings(mapping_id TEXT PRIMARY KEY,supplier_id TEXT NOT NULL,supplier_reference TEXT NOT NULL,supplier_ean TEXT,product_key TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(supplier_id,supplier_reference));
CREATE TABLE IF NOT EXISTS supplier_invoices(invoice_id TEXT PRIMARY KEY,supplier_id TEXT NOT NULL,invoice_number TEXT NOT NULL,invoice_date TEXT NOT NULL,due_date TEXT,currency TEXT NOT NULL,total_ht TEXT,total_tax TEXT,total_ttc TEXT,status TEXT NOT NULL,source TEXT NOT NULL,document_reference TEXT,imported_at TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,UNIQUE(supplier_id,invoice_number));
CREATE TABLE IF NOT EXISTS supplier_invoice_lines(invoice_line_id TEXT PRIMARY KEY,invoice_id TEXT NOT NULL,product_key TEXT,supplier_reference TEXT,description TEXT NOT NULL,quantity TEXT,unit_price_ht TEXT,tax_rate TEXT,tax_amount TEXT,total_ht TEXT,total_ttc TEXT,mapping_status TEXT NOT NULL,receipt_line_id TEXT,FOREIGN KEY(invoice_id) REFERENCES supplier_invoices(invoice_id));
CREATE TABLE IF NOT EXISTS purchase_cost_events(cost_event_id TEXT PRIMARY KEY,product_key TEXT NOT NULL,supplier_id TEXT NOT NULL,purchase_order_id TEXT,receipt_id TEXT,receipt_line_id TEXT,invoice_id TEXT,invoice_line_id TEXT,received_at TEXT NOT NULL,quantity TEXT NOT NULL,unit_cost_ht TEXT,unit_tax TEXT,unit_cost_ttc TEXT,allocated_landed_cost TEXT,currency TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,availability TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory_cost_lots(cost_lot_id TEXT PRIMARY KEY,cost_event_id TEXT NOT NULL UNIQUE,product_key TEXT NOT NULL,receipt_line_id TEXT,received_at TEXT NOT NULL,initial_quantity TEXT NOT NULL,remaining_quantity TEXT NOT NULL,unit_cost_ht TEXT NOT NULL,unit_tax TEXT,unit_cost_ttc TEXT,landed_cost_unit TEXT,currency TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sale_cost_allocations(allocation_id TEXT PRIMARY KEY,sale_event_id TEXT NOT NULL,product_key TEXT NOT NULL,cost_lot_id TEXT NOT NULL,quantity TEXT NOT NULL,unit_cost TEXT NOT NULL,total_cost TEXT NOT NULL,allocation_method TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(sale_event_id,cost_lot_id));
CREATE TABLE IF NOT EXISTS purchase_cost_diagnostics(diagnostic_id TEXT PRIMARY KEY,kind TEXT NOT NULL,entity_id TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(kind,entity_id));
CREATE INDEX IF NOT EXISTS ix_pcm_supplier ON supplier_product_mappings(supplier_id);
CREATE INDEX IF NOT EXISTS ix_invoice_supplier ON supplier_invoices(supplier_id);
CREATE INDEX IF NOT EXISTS ix_cost_product_date ON purchase_cost_events(product_key,received_at);
CREATE INDEX IF NOT EXISTS ix_cost_receipt ON purchase_cost_events(receipt_id);
CREATE INDEX IF NOT EXISTS ix_cost_invoice ON purchase_cost_events(invoice_id);
CREATE INDEX IF NOT EXISTS ix_lot_product_fifo ON inventory_cost_lots(product_key,received_at,cost_lot_id);
CREATE INDEX IF NOT EXISTS ix_lot_remaining ON inventory_cost_lots(remaining_quantity);
CREATE INDEX IF NOT EXISTS ix_allocation_sale ON sale_cost_allocations(sale_event_id);
"""

def now(): return datetime.now(timezone.utc).isoformat()
def dec(value, required=False):
    if value is None or str(value).strip()=="":
        if required: raise ValueError("missing decimal")
        return None
    try: value=Decimal(str(value))
    except InvalidOperation as exc: raise ValueError("invalid decimal") from exc
    if not value.is_finite(): raise ValueError("invalid decimal")
    return value
def money(value): return str(dec(value,True).quantize(TWOPLACES,rounding=ROUND_HALF_UP))

class PurchaseCostLedger:
    """Historically traceable cost evidence plus deterministic FIFO projection."""
    def __init__(self,path,catalogue=None,tolerance="0.02"):
        self.db=sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row
        ensure_schema(self.db, SCHEMA, owner="Purchase Ledger")
        self.catalogue=catalogue;self.tolerance=dec(tolerance,True)
    def _rows(self,sql,args=()): return [dict(r) for r in self.db.execute(sql,args)]
    def map_product(self,supplier_id,reference,product_key,*,ean=None,actor="authenticated",source="MANUAL"):
        if not supplier_id or not reference or not product_key: raise ValueError("supplier, reference and product are required")
        if self.catalogue and not self.catalogue.get(product_key): raise ValueError("canonical product not found")
        stamp=now();mid="spm:"+str(uuid.uuid4())
        with self.db:self.db.execute("""INSERT INTO supplier_product_mappings (mapping_id,supplier_id,supplier_reference,supplier_ean,product_key,status,source,actor,created_at,updated_at) VALUES(?,?,?,?,?,'MATCHED',?,?,?,?) ON CONFLICT(supplier_id,supplier_reference) DO UPDATE SET supplier_ean=excluded.supplier_ean,product_key=excluded.product_key,status='MATCHED',source=excluded.source,actor=excluded.actor,updated_at=excluded.updated_at""",(mid,supplier_id,reference,ean,product_key,source,actor,stamp,stamp))
        return dict(self.db.execute("SELECT * FROM supplier_product_mappings WHERE supplier_id=? AND supplier_reference=?",(supplier_id,reference)).fetchone())
    def resolve(self,supplier_id,reference=None,ean=None,product_key=None):
        if product_key and (not self.catalogue or self.catalogue.get(product_key)): return product_key,"MATCHED"
        rows=self._rows("SELECT product_key FROM supplier_product_mappings WHERE supplier_id=? AND supplier_reference=? AND status='MATCHED'",(supplier_id,reference)) if reference else []
        if len(rows)==1:return rows[0]["product_key"],"MATCHED"
        candidates=[]
        if self.catalogue:
            for p in self.catalogue.all():
                if (ean and p.ean==ean) or (reference and p.reference==reference): candidates.append(p.drcloud_product_key)
        unique=set(candidates)
        return (next(iter(unique)),"MATCHED") if len(unique)==1 else (None,"AMBIGUOUS" if unique else "UNMATCHED")
    def preview_csv(self,content):
        rows=list(csv.DictReader(io.StringIO(content))); errors=[]; output=[]; seen=set()
        for n,row in enumerate(rows,2):
            required=("supplier_id","invoice_number","invoice_date","description")
            missing=[x for x in required if not str(row.get(x) or "").strip()]
            key=(row.get("supplier_id"),row.get("invoice_number"))
            duplicate=key in seen or bool(self.db.execute("SELECT 1 FROM supplier_invoices WHERE supplier_id=? AND invoice_number=?",key).fetchone());seen.add(key)
            product,status=self.resolve(str(row.get("supplier_id") or ""),row.get("supplier_reference"),row.get("ean"),row.get("product_key"))
            try:
                for field in ("quantity","unit_price_ht","tax_rate","tax_amount","total_ht","total_ttc"): dec(row.get(field))
            except ValueError: missing.append("invalid amount")
            if missing:errors.append({"line":n,"errors":missing})
            output.append({**row,"product_key":product,"mapping_status":status,"duplicate":duplicate})
        return {"preview_id":hashlib.sha256(content.encode()).hexdigest(),"rows":output,"errors":errors,"duplicates":sum(x["duplicate"] for x in output),"matched":sum(x["mapping_status"]=="MATCHED" for x in output),"unmatched":sum(x["mapping_status"]=="UNMATCHED" for x in output),"ambiguous":sum(x["mapping_status"]=="AMBIGUOUS" for x in output),"mutated":False}
    def apply_csv(self,content,preview_id,actor="authenticated"):
        preview=self.preview_csv(content)
        if preview["preview_id"]!=preview_id or preview["errors"]: raise ValueError("a valid matching preview is required")
        created=[]
        for row in preview["rows"]:
            if row["duplicate"]: continue
            created.append(self.create_invoice(row,[row],actor=actor))
        return {"created":len(created),"duplicates":preview["duplicates"],"invoice_ids":[x["invoice_id"] for x in created]}
    def create_invoice(self,data,lines,actor="authenticated"):
        stamp=now();iid="sinv:"+str(uuid.uuid4());supplier=str(data.get("supplier_id") or "");number=str(data.get("invoice_number") or "")
        if not supplier or not number or not data.get("invoice_date"): raise ValueError("supplier, invoice number and date are required")
        idem=str(data.get("idempotency_key") or hashlib.sha256(f"{supplier}|{number}".encode()).hexdigest())
        values=(iid,supplier,number,data["invoice_date"],data.get("due_date"),str(data.get("currency") or "EUR"),*(money(data.get(x)) if data.get(x) not in (None,"") else None for x in ("total_ht","total_tax","total_ttc")),"DRAFT",str(data.get("source") or "MANUAL"),data.get("document_reference"),stamp,idem)
        try:
            with self.db:
                self.db.execute("INSERT INTO supplier_invoices (invoice_id,supplier_id,invoice_number,invoice_date,due_date,currency,total_ht,total_tax,total_ttc,status,source,document_reference,imported_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
                for item in lines:
                    product,status=self.resolve(supplier,item.get("supplier_reference"),item.get("ean"),item.get("product_key"))
                    self.db.execute("INSERT INTO supplier_invoice_lines (invoice_line_id,invoice_id,product_key,supplier_reference,description,quantity,unit_price_ht,tax_rate,tax_amount,total_ht,total_ttc,mapping_status,receipt_line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",("sil:"+str(uuid.uuid4()),iid,product,item.get("supplier_reference"),str(item.get("description") or ""),*(money(item.get(x)) if item.get(x) not in (None,"") else None for x in ("quantity","unit_price_ht","tax_rate","tax_amount","total_ht","total_ttc")),status,item.get("receipt_line_id")))
        except sqlite3.IntegrityError as exc: raise ValueError("duplicate supplier invoice") from exc
        return self.invoice(iid)
    def invoice(self,iid):
        row=self.db.execute("SELECT * FROM supplier_invoices WHERE invoice_id=?",(iid,)).fetchone()
        if not row: raise KeyError("supplier invoice not found")
        return {**dict(row),"lines":self._rows("SELECT * FROM supplier_invoice_lines WHERE invoice_id=?",(iid,))}
    def control_invoice(self,iid):
        inv=self.invoice(iid);lines=inv["lines"]
        if not lines or any(x["total_ht"] is None or x["tax_amount"] is None for x in lines): return {"status":"MISSING_DATA","differences":[]}
        ht=sum((dec(x["total_ht"],True) for x in lines),Decimal());tax=sum((dec(x["tax_amount"],True) for x in lines),Decimal());ttc=ht+tax; diffs=[]
        for label,actual,declared in (("HT",ht,inv["total_ht"]),("TAX",tax,inv["total_tax"]),("TTC",ttc,inv["total_ttc"])):
            if declared is None: diffs.append("MISSING_DATA")
            elif abs(actual-dec(declared,True))>self.tolerance: diffs.append("TAX_DIFFERENCE" if label=="TAX" else "TOTAL_DIFFERENCE")
        return {"status":diffs[0] if diffs else "MATCHED","differences":sorted(set(diffs)),"computed":{"total_ht":money(ht),"total_tax":money(tax),"total_ttc":money(ttc)},"tolerance":str(self.tolerance)}
    def validate_invoice(self,iid,actor="authenticated"):
        inv=self.invoice(iid);control=self.control_invoice(iid)
        if control["status"]!="MATCHED" or any(x["mapping_status"]!="MATCHED" for x in inv["lines"]): raise ValueError("invoice requires human reconciliation")
        with self.db:self.db.execute("UPDATE supplier_invoices SET status='VALIDATED' WHERE invoice_id=?",(iid,))
        # A validated invoice can confirm a receipt only through an explicit
        # receipt_line_id.  Missing links remain visible and create no lot.
        for line in inv["lines"]:
            if not line["receipt_line_id"] or line["unit_price_ht"] is None: continue
            receipt=self.db.execute("""SELECT gl.received_quantity,gl.product_key,gl.receipt_id,g.received_at,g.purchase_order_id,p.supplier_id
              FROM goods_receipt_lines gl JOIN goods_receipts g ON g.receipt_id=gl.receipt_id
              JOIN purchase_orders p ON p.purchase_order_id=g.purchase_order_id
              WHERE gl.receipt_line_id=? AND g.status='APPLIED'""",(line["receipt_line_id"],)).fetchone()
            if receipt and receipt["supplier_id"]==inv["supplier_id"] and receipt["product_key"]==line["product_key"]:
                unit_tax=dec(line["tax_amount"])/dec(receipt["received_quantity"],True) if line["tax_amount"] is not None else None
                self.record_receipt_cost(product_key=receipt["product_key"],supplier_id=inv["supplier_id"],quantity=receipt["received_quantity"],received_at=receipt["received_at"],unit_cost_ht=line["unit_price_ht"],receipt_line_id=line["receipt_line_id"],receipt_id=receipt["receipt_id"],purchase_order_id=receipt["purchase_order_id"],invoice_id=iid,invoice_line_id=line["invoice_line_id"],tax_amount=unit_tax,unit_cost_ttc=(dec(line["unit_price_ht"],True)+(unit_tax or Decimal())),currency=inv["currency"],status="CONFIRMED",actor=actor)
        return self.invoice(iid)
    def record_receipt_cost(self,*,product_key,supplier_id,quantity,received_at,unit_cost_ht,receipt_line_id,receipt_id=None,purchase_order_id=None,invoice_id=None,invoice_line_id=None,tax_amount=None,unit_cost_ttc=None,landed_cost=None,currency="EUR",status="CONFIRMED",actor="system"):
        if status not in STATUSES: raise ValueError("invalid cost status")
        q=dec(quantity,True);cost=dec(unit_cost_ht); tax=dec(tax_amount); landed=dec(landed_cost)
        if q<=0: raise ValueError("quantity must be positive")
        if status=="CONFIRMED" and cost is None: raise ValueError("confirmed cost requires evidence")
        # One physical receipt line owns one cost event/lot.  Invoice validation
        # must never duplicate the quantity already valued from the order.
        key=f"cost:{receipt_line_id}";eid="pce:"+str(uuid.uuid4());stamp=now()
        values=(eid,product_key,supplier_id,purchase_order_id,receipt_id,receipt_line_id,invoice_id,invoice_line_id,received_at,money(q),money(cost) if cost is not None else None,money(tax) if tax is not None else None,money(unit_cost_ttc) if unit_cost_ttc is not None else None,money(landed) if landed is not None else None,currency,"SUPPLIER_INVOICE" if invoice_id else "GOODS_RECEIPT",status,"AVAILABLE" if cost is not None else "UNAVAILABLE",key,stamp,actor)
        try:
            with self.db:
                self.db.execute("INSERT INTO purchase_cost_events (cost_event_id,product_key,supplier_id,purchase_order_id,receipt_id,receipt_line_id,invoice_id,invoice_line_id,received_at,quantity,unit_cost_ht,unit_tax,unit_cost_ttc,allocated_landed_cost,currency,source,status,availability,idempotency_key,created_at,actor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
                if status=="CONFIRMED" and cost is not None:
                    self.db.execute("INSERT INTO inventory_cost_lots (cost_lot_id,cost_event_id,product_key,receipt_line_id,received_at,initial_quantity,remaining_quantity,unit_cost_ht,unit_tax,unit_cost_ttc,landed_cost_unit,currency,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",("lot:"+str(uuid.uuid4()),eid,product_key,receipt_line_id,received_at,money(q),money(q),money(cost),money(tax) if tax is not None else None,money(unit_cost_ttc) if unit_cost_ttc is not None else None,money(landed) if landed is not None else None,currency,"OPEN"))
        except sqlite3.IntegrityError:
            return dict(self.db.execute("SELECT * FROM purchase_cost_events WHERE idempotency_key=?",(key,)).fetchone())
        return dict(self.db.execute("SELECT * FROM purchase_cost_events WHERE cost_event_id=?",(eid,)).fetchone())
    def allocate_sale(self,sale_event_id,product_key,quantity):
        requested=abs(dec(quantity,True)); existing=self._rows("SELECT * FROM sale_cost_allocations WHERE sale_event_id=?",(sale_event_id,))
        if existing:return self._allocation_result(requested,existing)
        remaining=requested;created=[]
        with self.db:
            lots=self.db.execute("SELECT * FROM inventory_cost_lots WHERE product_key=? AND status='OPEN' AND CAST(remaining_quantity AS REAL)>0 ORDER BY received_at,cost_lot_id",(product_key,)).fetchall()
            for lot in lots:
                if remaining<=0:break
                take=min(remaining,dec(lot["remaining_quantity"],True));unit=dec(lot["unit_cost_ht"],True)+(dec(lot["landed_cost_unit"]) or Decimal())
                row={"allocation_id":"sca:"+str(uuid.uuid4()),"sale_event_id":sale_event_id,"product_key":product_key,"cost_lot_id":lot["cost_lot_id"],"quantity":money(take),"unit_cost":money(unit),"total_cost":money(take*unit),"allocation_method":"FIFO","created_at":now()}
                self.db.execute("INSERT INTO sale_cost_allocations (allocation_id,sale_event_id,product_key,cost_lot_id,quantity,unit_cost,total_cost,allocation_method,created_at) VALUES(:allocation_id,:sale_event_id,:product_key,:cost_lot_id,:quantity,:unit_cost,:total_cost,:allocation_method,:created_at)",row)
                left=dec(lot["remaining_quantity"],True)-take;self.db.execute("UPDATE inventory_cost_lots SET remaining_quantity=?,status=? WHERE cost_lot_id=?",(money(left),"EXHAUSTED" if left==0 else "OPEN",lot["cost_lot_id"]));created.append(row);remaining-=take
            if remaining:self.db.execute("INSERT OR REPLACE INTO purchase_cost_diagnostics (diagnostic_id,kind,entity_id,details_json,created_at) VALUES(?,?,?,?,?)",("diag:"+str(uuid.uuid4()),"SALE_COST_UNCOVERED",sale_event_id,json.dumps({"quantity":str(remaining)}),now()))
        return self._allocation_result(requested,created)
    def _allocation_result(self,requested,rows):
        covered=sum((dec(x["quantity"],True) for x in rows),Decimal());total=sum((dec(x["total_cost"],True) for x in rows),Decimal())
        return {"sale_event_id":rows[0]["sale_event_id"] if rows else None,"requested_quantity":money(requested),"covered_quantity":money(covered),"uncovered_quantity":money(requested-covered),"coverage_percent":money(Decimal(100)*covered/requested) if requested else "100.00","total_cost":money(total),"allocations":rows}
    def stock_value(self,physical=None):
        rows=self._rows("SELECT product_key,SUM(CAST(remaining_quantity AS REAL)) quantity,SUM(CAST(remaining_quantity AS REAL)*(CAST(unit_cost_ht AS REAL)+COALESCE(CAST(landed_cost_unit AS REAL),0))) value_ht,MAX(received_at) freshness FROM inventory_cost_lots WHERE status='OPEN' GROUP BY product_key")
        products=[]
        for row in rows:
            actual=dec((physical or {}).get(row["product_key"],row["quantity"]),True);covered=min(max(actual,Decimal()),dec(row["quantity"],True));lotq=dec(row["quantity"],True)
            products.append({**row,"quantity":money(actual),"covered_quantity":money(covered),"uncovered_quantity":money(max(actual-covered,Decimal())),"value_ht":money(dec(row["value_ht"],True)*covered/lotq) if lotq else "0.00","coverage_percent":money(covered*100/actual) if actual>0 else "100.00"})
        return {"method":"FIFO","products":products,"value_ht":money(sum((dec(x["value_ht"],True) for x in products),Decimal())),"covered_quantity":money(sum((dec(x["covered_quantity"],True) for x in products),Decimal())),"uncovered_quantity":money(sum((dec(x["uncovered_quantity"],True) for x in products),Decimal())),"freshness":max((x["freshness"] for x in products),default=None)}
    def profitability(self,start=None,end=None):
        where="";args=[]
        if start:where+=" AND s.sold_at>=?";args.append(start)
        if end:where+=" AND s.sold_at<?";args.append(end)
        rows=self._rows("""SELECT s.product_key,SUM(CAST(s.quantity AS REAL)) units,SUM(CAST(s.line_total_ht AS REAL)) revenue_ht,SUM(COALESCE((SELECT SUM(CAST(a.total_cost AS REAL)) FROM sale_cost_allocations a WHERE a.sale_event_id=s.sale_event_id),0)) cost,SUM(CASE WHEN EXISTS(SELECT 1 FROM sale_cost_allocations a WHERE a.sale_event_id=s.sale_event_id) THEN CAST(s.quantity AS REAL) ELSE 0 END) covered FROM sale_events s WHERE s.event_kind='SALE' AND s.product_key IS NOT NULL"""+where+" GROUP BY s.product_key",args)
        for r in rows:
            revenue=dec(r["revenue_ht"]);cost=dec(r["cost"],True);units=dec(r["units"],True);covered=dec(r["covered"],True);r.update(gross_margin=money(revenue-cost) if revenue is not None and covered==units else None,coverage_percent=money(covered*100/units) if units else "100.00",status="COMPLETE" if covered==units else "PARTIAL")
        return {"products":rows,"method":"CONFIRMED_FIFO","missing_cost_is_zero":False}
