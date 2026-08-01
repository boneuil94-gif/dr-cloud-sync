"""Operational sales ingestion, deliberately separated from stock and external writes."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv, io, json, sqlite3
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4
from .sales import SaleEvent, SaleKind, SaleSource, SalesLedger, _decimal, _now, _utc
from .shopcaisse import ShopCaisseClient
from .connector_diagnostics import sanitize


@dataclass(frozen=True)
class CanonicalSaleLine:
    external_line_id: str
    quantity: Decimal
    kind: str = "SALE"
    source_product_id: str|None = None
    source_variant_id: str|None = None
    source_reference: str|None = None
    source_ean: str|None = None
    product_key: str|None = None
    unit_price_ttc: Decimal|None = None
    unit_price_ht: Decimal|None = None
    line_total_ttc: Decimal|None = None
    line_total_ht: Decimal|None = None
    tax_rate: Decimal|None = None

@dataclass(frozen=True)
class CanonicalPayment:
    external_payment_id: str
    payment_type: str
    amount: Decimal
    name: str|None = None
    description: str|None = None


@dataclass(frozen=True)
class CanonicalSale:
    source: str
    external_sale_id: str
    sold_at: str
    timezone: str
    channel: str
    currency: str
    status: str
    lines: tuple[CanonicalSaleLine,...]
    location: str|None = None
    source_updated_at: str|None = None
    payments: tuple[CanonicalPayment,...] = ()


@dataclass(frozen=True)
class ProviderBatch:
    sales: tuple[CanonicalSale,...]
    cursor: str|None = None


class SalesProvider(Protocol):
    source: str
    @property
    def configured(self)->bool: ...
    def fetch(self,*,cursor: str|None=None,since: datetime|None=None)->ProviderBatch: ...


OPERATIONAL_SCHEMA="""
CREATE TABLE IF NOT EXISTS sales(
 sale_id TEXT PRIMARY KEY,source TEXT NOT NULL,external_sale_id TEXT NOT NULL,sold_at TEXT NOT NULL,
 timezone TEXT NOT NULL,channel TEXT NOT NULL,location TEXT,currency TEXT NOT NULL,status TEXT NOT NULL,
 source_updated_at TEXT,imported_at TEXT NOT NULL,UNIQUE(source,external_sale_id));
CREATE TABLE IF NOT EXISTS sale_lines(
 sale_line_id TEXT PRIMARY KEY,sale_id TEXT NOT NULL,external_line_id TEXT NOT NULL,source_product_id TEXT,
 source_variant_id TEXT,source_reference TEXT,source_ean TEXT,product_key TEXT,quantity TEXT NOT NULL,
 unit_price_ttc TEXT,unit_price_ht TEXT,line_total_ttc TEXT,line_total_ht TEXT,tax_rate TEXT,
 mapping_status TEXT NOT NULL,mapping_reason TEXT,event_kind TEXT NOT NULL,UNIQUE(sale_id,external_line_id,event_kind),
 FOREIGN KEY(sale_id) REFERENCES sales(sale_id));
CREATE TABLE IF NOT EXISTS sale_payments(
 payment_id TEXT PRIMARY KEY,sale_id TEXT NOT NULL,external_payment_id TEXT NOT NULL,payment_type TEXT NOT NULL,
 amount TEXT NOT NULL,name TEXT,description TEXT,UNIQUE(sale_id,external_payment_id),
 FOREIGN KEY(sale_id) REFERENCES sales(sale_id));
CREATE TABLE IF NOT EXISTS shopcaisse_stock_observations(
 store_id TEXT NOT NULL,item_id TEXT NOT NULL,stock TEXT NOT NULL,reserved_customers TEXT NOT NULL,
 reserved_suppliers TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(store_id,item_id));
CREATE TABLE IF NOT EXISTS sales_product_mappings(
 mapping_id TEXT PRIMARY KEY,source TEXT NOT NULL,external_product_id TEXT NOT NULL,external_variant_id TEXT NOT NULL DEFAULT '',
 source_ean TEXT,source_reference TEXT,product_key TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,actor TEXT NOT NULL,UNIQUE(source,external_product_id,external_variant_id));
CREATE TABLE IF NOT EXISTS sales_sync_states(
 source TEXT PRIMARY KEY,last_success_at TEXT,last_attempt_at TEXT,status TEXT NOT NULL,cursor TEXT,last_error TEXT,
 imported_count INTEGER NOT NULL DEFAULT 0,unmatched_count INTEGER NOT NULL DEFAULT 0,last_report_json TEXT);
"""


class ShopCaisseCSVProvider:
    """Adapter for an operator-provided ShopCaisse sales CSV; no API is invented."""
    source=SaleSource.SHOPCAISSE.value
    REQUIRED={"sale_id","line_id","sold_at","quantity"}
    def __init__(self,content: str): self.content=content
    @property
    def configured(self): return bool(self.content.strip())
    def fetch(self,*,cursor=None,since=None)->ProviderBatch:
        reader=csv.DictReader(io.StringIO(self.content)); missing=self.REQUIRED-set(reader.fieldnames or ())
        if missing: raise ValueError("ShopCaisse CSV columns missing: "+", ".join(sorted(missing)))
        grouped={}
        for row in reader:
            sale_id=str(row["sale_id"]).strip(); sold=_utc(str(row["sold_at"])).isoformat()
            if since and _utc(sold)<since.astimezone(timezone.utc): continue
            line=CanonicalSaleLine(str(row["line_id"]).strip(),_decimal(row["quantity"],required=True),str(row.get("event_kind") or "SALE").upper(),
                str(row.get("item_id") or "") or None,str(row.get("variant_id") or "") or None,str(row.get("reference") or "") or None,
                str(row.get("ean") or "") or None,None,*(_decimal(row.get(x)) for x in ("unit_price_ttc","unit_price_ht","line_total_ttc","line_total_ht","tax_rate")))
            key=(sale_id,sold); grouped.setdefault(key,[]).append(line)
        sales=tuple(CanonicalSale(self.source,k[0],k[1],"UTC","STORE",str(rows[0].__dict__ and "EUR"),"COMPLETED",tuple(rows)) for k,rows in grouped.items())
        return ProviderBatch(sales,max((s.sold_at for s in sales),default=cursor))

class ShopCaisseSalesProvider(ShopCaisseCSVProvider):
    """Automatic ingestion of the real CSV export deposited in a protected inbox."""
    def __init__(self,inbox: Path): self.inbox=Path(inbox); super().__init__("")
    @property
    def configured(self): return self.inbox.is_dir()
    def fetch(self,*,cursor=None,since=None):
        files=sorted(self.inbox.glob("*.csv")); content=""; header=None
        for path in files:
            rows=path.read_text(encoding="utf-8-sig").splitlines()
            if not rows: continue
            if header is None: header=rows[0];content+=header+"\n"
            if rows[0]!=header: raise ValueError(f"ShopCaisse CSV header mismatch: {path.name}")
            content+="\n".join(rows[1:])+"\n"
        self.content=content
        return super().fetch(cursor=cursor,since=since)


class ShopCaisseAPISalesProvider:
    """Read-only adapter for the documented ShopCaisse store sales API."""
    source=SaleSource.SHOPCAISSE.value
    def __init__(self,client: ShopCaisseClient,db: sqlite3.Connection,*,stock_board_type="DEFAULT"):
        self.client,self.db,self.stock_board_type=client,db,stock_board_type
    @property
    def configured(self): return True
    def fetch(self,*,cursor=None,since=None)->ProviderBatch:
        # One millisecond overlap makes boundary records safe; ledger keys remove replays.
        from_ms=int(_utc(cursor).timestamp()*1000)-1 if cursor else (int(since.timestamp()*1000) if since else None)
        sales=[]; newest=cursor
        for store in self.client.pull_stores():
            store_id=str(store["id"])
            for row in self.client.pull_store_sales(store_id,from_ms=from_ms):
                timestamp=int(row["timestamp"]); sold=datetime.fromtimestamp(timestamp/1000,timezone.utc).isoformat()
                newest=max(newest or sold,sold)
                lines=[]
                for line in row.get("lines",[]):
                    price=line.get("price") or {}; item=line.get("item") or {}
                    lines.append(CanonicalSaleLine(str(line["id"]),_decimal(line["quantity"],required=True),
                        "REFUND" if str(row.get("type","")).upper() in {"REFUND","RETURN"} else "SALE",
                        str(item.get("id") or "") or None,None,str(item.get("reference") or "") or None,
                        (str((item.get("barcodes") or [""])[0]) or None),None,_decimal(line.get("unitPrice")),None,
                        _decimal(price.get("vatIncluded")),_decimal(price.get("vatExcluded")),_decimal(price.get("vatRate"))))
                payments=tuple(CanonicalPayment(str(p.get("id") or f"{row['id']}:{i}"),str(p.get("type") or "UNKNOWN"),
                    _decimal(p.get("amount"),required=True),p.get("name"),p.get("description")) for i,p in enumerate(row.get("payments",[])))
                sales.append(CanonicalSale(self.source,str(row["id"]),sold,"UTC","STORE","EUR",str(row.get("status") or "UNKNOWN").upper(),
                    tuple(lines),store_id,sold,payments))
            observed=_now()
            stocks=self.client.pull_store_stocks(store_id,self.stock_board_type)
            with self.db:
                for stock in stocks:
                    self.db.execute("INSERT INTO shopcaisse_stock_observations (store_id,item_id,stock,reserved_customers,reserved_suppliers,observed_at) VALUES(?,?,?,?,?,?) ON CONFLICT(store_id,item_id) DO UPDATE SET stock=excluded.stock,reserved_customers=excluded.reserved_customers,reserved_suppliers=excluded.reserved_suppliers,observed_at=excluded.observed_at",
                        (store_id,str(stock["item"]),str(stock["stock"]),str(stock.get("reservedForCustomers",0)),str(stock.get("reservedForSuppliers",0)),observed))
        return ProviderBatch(tuple(sales),newest)


class PrestaShopSalesProvider:
    """GET-only paid-order adapter. Accepted state ids must be configured explicitly."""
    source=SaleSource.PRESTASHOP.value
    def __init__(self,client,paid_state_ids: Sequence[str|int],*,cancelled_state_ids=(),refunded_state_ids=(),partially_refunded_state_ids=()):
        self.client=client; self.paid={str(x) for x in paid_state_ids}
        self.cancelled={str(x) for x in cancelled_state_ids}; self.refunded={str(x) for x in refunded_state_ids}
        self.partially_refunded={str(x) for x in partially_refunded_state_ids}
    @property
    def configured(self): return bool(self.client and self.paid)

    @staticmethod
    def _datetime(value: Any) -> str:
        """Normalize a PrestaShop date, whose API omits its documented UTC zone."""
        raw=str(value or "").strip()
        try:
            parsed=datetime.fromisoformat(raw.replace("Z","+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid PrestaShop date: {raw or '<empty>'}") from exc
        if parsed.tzinfo is None:
            parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    def fetch(self,*,cursor=None,since=None)->ProviderBatch:
        if not self.paid: raise RuntimeError("PRESTASHOP_PAID_STATE_IDS is required")
        details={}
        for row in self.client.iter_resource("order_details"): details.setdefault(str(row.get("id_order")),[]).append(row)
        result=[]
        for order in self.client.iter_resource("orders"):
            state=str(order.get("current_state"))
            if state not in self.paid|self.cancelled|self.refunded|self.partially_refunded: continue
            try:
                sold=self._datetime(order.get("date_upd") or order.get("date_add"))
            except ValueError as exc:
                error=ValueError(f"PrestaShop sale {order.get('id') or '<unknown>'}: {exc}")
                error.sale_id=str(order.get("id") or "unknown")
                raise error from exc
            if since and _utc(sold)<since.astimezone(timezone.utc): continue
            lines=[]
            for row in details.get(str(order.get("id")),[]):
                quantity=_decimal(row.get("product_quantity"),required=True)
                ttc=_decimal(row.get("total_price_tax_incl")); ht=_decimal(row.get("total_price_tax_excl"))
                # State policy is explicit.  A partial refund cannot safely be valued
                # from an order state alone, so it remains absent until a verified
                # structured refund line is available.
                kind="CANCELLATION" if state in self.cancelled else "REFUND" if state in self.refunded else "SALE"
                if state in self.partially_refunded: continue
                lines.append(CanonicalSaleLine(str(row.get("id")),quantity,kind,str(row.get("product_id") or "") or None,
                    str(row.get("product_attribute_id") or "") or None,str(row.get("product_reference") or "") or None,
                    str(row.get("product_ean13") or "") or None,None,_decimal(row.get("unit_price_tax_incl")),
                    _decimal(row.get("unit_price_tax_excl")),ttc,ht,None))
            status="CANCELLED" if state in self.cancelled else "REFUNDED" if state in self.refunded else "PARTIALLY_REFUNDED" if state in self.partially_refunded else "COMPLETED"
            result.append(CanonicalSale(self.source,str(order["id"]),sold,"UTC","ECOMMERCE",str(order.get("id_currency") or "EUR"),status,tuple(lines),source_updated_at=sold))
        return ProviderBatch(tuple(result),max((s.source_updated_at for s in result),default=cursor))


class SalesSyncService:
    def __init__(self,ledger: SalesLedger,providers: Mapping[str,SalesProvider]|None=None,*,stale_after_hours=48):
        self.ledger=ledger;self.db=ledger.db;self.providers=dict(providers or {});self.stale_after_hours=stale_after_hours
        with self.db:
            self.db.executescript(OPERATIONAL_SCHEMA)
            columns={row[1] for row in self.db.execute("PRAGMA table_info(sales_sync_states)")}
            if "last_report_json" not in columns:self.db.execute("ALTER TABLE sales_sync_states ADD COLUMN last_report_json TEXT")

    def _mapping(self,source,line):
        row=self.db.execute("SELECT product_key FROM sales_product_mappings WHERE source=? AND external_product_id=? AND external_variant_id=? AND status='ACTIVE'",
            (source,line.source_product_id or "",line.source_variant_id or "")).fetchone()
        if row:return row[0],"MATCHED","explicit mapping"
        candidates=self.ledger.resolve_candidates({"product_key":line.product_key,"prestashop_combination_id":line.source_variant_id if source=="PRESTASHOP" else None,
            "shopcaisse_item_id":line.source_product_id if source=="SHOPCAISSE" else None,"ean":line.source_ean,"reference":line.source_reference})
        if len(candidates)==1:return candidates[0],"MATCHED","exact catalogue identity"
        if len(candidates)>1:return None,"AMBIGUOUS","multiple exact catalogue identities"
        return None,"UNMATCHED","no exact catalogue identity"

    def sync(self,source: str,*,since: datetime|None=None,force=False,actor="authenticated"):
        source=SaleSource(source.upper()).value; provider=self.providers.get(source)
        if not provider or not provider.configured: raise RuntimeError(f"{source} sales provider unavailable")
        state=self.db.execute("SELECT * FROM sales_sync_states WHERE source=?",(source,)).fetchone();cursor=None if force or not state else state["cursor"]
        attempt=_now(); report={"source":source,"imported":0,"sales":0,"payments":0,"duplicates":0,"unmatched":0,"ambiguous":0,"invalid":0,"refunds":0,"errors":[],"failure_details":[]}
        with self.db:
            self.db.execute("INSERT INTO sales_sync_states(source,last_attempt_at,status) VALUES(?,?,'RUNNING') ON CONFLICT(source) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,status='RUNNING',last_error=NULL",(source,attempt))
            self.ledger._audit("SALES_SYNC_STARTED",actor,{"source":source})
        try:
            batch=provider.fetch(cursor=cursor,since=since)
            with self.db:
                for sale in batch.sales:
                    report["sales"]+=1
                    sale_id=f"sale:{source}:{sale.external_sale_id}"
                    self.db.execute("INSERT OR IGNORE INTO sales (sale_id,source,external_sale_id,sold_at,timezone,channel,location,currency,status,source_updated_at,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(sale_id,source,sale.external_sale_id,sale.sold_at,sale.timezone,sale.channel,sale.location,sale.currency,sale.status,sale.source_updated_at,_now()))
                    for line in sale.lines:
                        try:
                            if not line.external_line_id or line.quantity<=0: raise ValueError("invalid line identity or quantity")
                            product,status,reason=self._mapping(source,line)
                            if status!="MATCHED": report[status.lower()]+=1
                            event=SaleEvent(source,sale.external_sale_id,line.external_line_id,sale.sold_at,sale.timezone,SaleKind(line.kind).value,product,line.quantity,
                                self.ledger.key(source,sale.external_sale_id,line.external_line_id,line.kind),line.unit_price_ttc,line.unit_price_ht,line.line_total_ttc,line.line_total_ht,
                                currency=sale.currency,channel=sale.channel,location=sale.location,raw_reference=line.source_reference,source_updated_at=sale.source_updated_at)
                            inserted=self.ledger.append(event,match_status=status)
                            if inserted: report["imported"]+=1
                            else: report["duplicates"]+=1
                            if line.kind in {"REFUND","RETURN","CANCELLATION"}:report["refunds"]+=1
                            self.db.execute("INSERT OR IGNORE INTO sale_lines (sale_line_id,sale_id,external_line_id,source_product_id,source_variant_id,source_reference,source_ean,product_key,quantity,unit_price_ttc,unit_price_ht,line_total_ttc,line_total_ht,tax_rate,mapping_status,mapping_reason,event_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(f"sale-line:{uuid4()}",sale_id,line.external_line_id,line.source_product_id,line.source_variant_id,line.source_reference,line.source_ean,product,str(line.quantity),*[str(x) if x is not None else None for x in (line.unit_price_ttc,line.unit_price_ht,line.line_total_ttc,line.line_total_ht,line.tax_rate)],status,reason,line.kind))
                        except (ValueError,TypeError) as exc:
                            report["invalid"]+=1
                            failure=self._failure(sale, exc, line_id=line.external_line_id,
                                stage="INGESTION_LINE", category="VALIDATION", retryable=False)
                            report["errors"].append({"sale":failure["sale"],"line":failure["line"],"error":failure["message"]})
                            report["failure_details"].append(failure)
                    for payment in sale.payments:
                        inserted=self.db.execute("INSERT OR IGNORE INTO sale_payments (payment_id,sale_id,external_payment_id,payment_type,amount,name,description) VALUES(?,?,?,?,?,?,?)",(f"payment:{source}:{sale.external_sale_id}:{payment.external_payment_id}",sale_id,payment.external_payment_id,payment.payment_type,str(payment.amount),payment.name,payment.description)).rowcount
                        report["payments"]+=inserted
                now=_now();self.db.execute("INSERT INTO sales_sync_states(source,last_success_at,last_attempt_at,status,cursor,last_error,imported_count,unmatched_count,last_report_json) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,status=excluded.status,cursor=excluded.cursor,last_error=NULL,imported_count=excluded.imported_count,unmatched_count=excluded.unmatched_count,last_report_json=excluded.last_report_json",(source,now,attempt,"SUCCESS",batch.cursor,None,report["imported"],report["unmatched"]+report["ambiguous"],json.dumps(report,ensure_ascii=False)))
                self.ledger._audit("SALES_SYNC_COMPLETED",actor,report)
            return report
        except Exception as exc:
            message=sanitize(str(exc),limit=300) or "Erreur d’import de vente"
            report["invalid"]+=1
            failure={"sale":getattr(exc,"sale_id",None),"line":getattr(exc,"line_id",None),
                "error":message,"message":message,"stage":getattr(exc,"stage","PROVIDER_FETCH"),
                "category":getattr(exc,"category",None) or "PROVIDER",
                "retryable":bool(getattr(exc,"retryable",False)),"permanent":not bool(getattr(exc,"retryable",False)),
                "sold_at":None,"amount":None,"currency":None,"store":None}
            report["errors"].append({"sale":failure["sale"],"line":failure["line"],"error":message})
            report["failure_details"].append(failure)
            with self.db:
                self.db.execute("UPDATE sales_sync_states SET status='ERROR',last_error=?,last_report_json=? WHERE source=?",(message,json.dumps(report,ensure_ascii=False),source));self.ledger._audit("SALES_SYNC_FAILED",actor,{"source":source,"error":message})
            raise

    @staticmethod
    def _failure(sale, exc, *, line_id=None, stage="INGESTION", category="VALIDATION", retryable=False):
        """Build the operator diagnostic without retaining raw provider payloads."""
        amount=sum((line.line_total_ttc for line in sale.lines if line.line_total_ttc is not None),Decimal(0))
        message=sanitize(str(exc),limit=300) or "Erreur d’import de vente"
        return {"sale":sale.external_sale_id,"line":line_id,"sold_at":sale.sold_at,
            "amount":str(amount) if any(line.line_total_ttc is not None for line in sale.lines) else None,
            "currency":sale.currency,"store":sale.location,"stage":stage,"category":category,
            "message":message,"error":message,"retryable":bool(retryable),"permanent":not bool(retryable)}

    def failed_sales(self,source="SHOPCAISSE"):
        """Return the last persisted failure report; this is diagnostic and read-only."""
        row=self.db.execute("SELECT last_report_json FROM sales_sync_states WHERE source=?",(source,)).fetchone()
        report=json.loads(row[0] or "{}") if row else {}
        failures=[]
        for item in report.get("failure_details") or report.get("errors",[]):
            message=sanitize(item.get("message") or item.get("error"),limit=300) or "Cause inconnue"
            retryable=bool(item.get("retryable",False))
            failures.append({"shopcaisse_id":item.get("sale"),"date":item.get("sold_at"),
                "amount":item.get("amount"),"currency":item.get("currency"),"store":item.get("store"),
                "stage":item.get("stage") or "INCONNUE","category":item.get("category") or "INCONNUE",
                "message":message,"retryable":retryable,"permanent":not retryable})
        return {"source":source,"count":len(failures),"failures":failures}

    def preview(self,provider: SalesProvider):
        batch=provider.fetch(); report={"source":provider.source,"sales":len(batch.sales),"lines":0,"matched":0,"unmatched":0,"ambiguous":0,"invalid":0,"quantity":0.0,"revenue_ttc":0.0,"date_from":None,"date_to":None,"duplicates":0}
        dates=[]
        for sale in batch.sales:
            dates.append(sale.sold_at)
            for line in sale.lines:
                report["lines"]+=1
                try:
                    _,status,_=self._mapping(provider.source,line);report[status.lower()]+=1
                    report["quantity"]+=float(line.quantity)
                    if line.line_total_ttc is not None:report["revenue_ttc"]+=float(line.line_total_ttc)
                    key=self.ledger.key(provider.source,sale.external_sale_id,line.external_line_id,line.kind)
                    report["duplicates"]+=bool(self.db.execute("SELECT 1 FROM sale_events WHERE idempotency_key=?",(key,)).fetchone())
                except Exception:report["invalid"]+=1
        report["date_from"]=min(dates) if dates else None;report["date_to"]=max(dates) if dates else None
        return report

    def create_mapping(self,source,external_product_id,external_variant_id,product_key,actor):
        if not any(p.drcloud_product_key==product_key for p in self.ledger.catalogue.all()):raise ValueError("unknown catalogue product")
        now=_now(); existing=self.db.execute("SELECT * FROM sales_product_mappings WHERE source=? AND external_product_id=? AND external_variant_id=?",(source,external_product_id,external_variant_id or "")).fetchone()
        event="SALES_MAPPING_CHANGED" if existing else "SALES_MAPPING_CREATED"
        with self.db:
            self.db.execute("INSERT INTO sales_product_mappings (mapping_id,source,external_product_id,external_variant_id,source_ean,source_reference,product_key,status,created_at,updated_at,actor) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,external_product_id,external_variant_id) DO UPDATE SET product_key=excluded.product_key,status='ACTIVE',updated_at=excluded.updated_at,actor=excluded.actor",(existing["mapping_id"] if existing else f"mapping:{uuid4()}",source,external_product_id,external_variant_id or "",None,None,product_key,"ACTIVE",existing["created_at"] if existing else now,now,actor))
            self.ledger._audit(event,actor,{"source":source,"external_product_id":external_product_id,"external_variant_id":external_variant_id or "","product_key":product_key})

    def diagnostics(self):
        now=datetime.now(timezone.utc); states={r["source"]:dict(r) for r in self.db.execute("SELECT * FROM sales_sync_states")}
        for source in ("SHOPCAISSE","PRESTASHOP"):
            value=states.setdefault(source,{"source":source,"status":"UNAVAILABLE","last_success_at":None,"last_error":None,"imported_count":0,"unmatched_count":0})
            if value.get("status")=="ERROR":value["freshness"]="ERROR"
            elif not value.get("last_success_at"):value["freshness"]="UNAVAILABLE"
            else:value["freshness"]="FRESH" if now-_utc(value["last_success_at"])<=timedelta(hours=self.stale_after_hours) else "STALE"
        for value in states.values():
            report=json.loads(value.pop("last_report_json",None) or "{}")
            value["failed_count"]=int(report.get("invalid",0))
            value["failed_sales"]=report.get("errors",[])
            value["sales_count"]=self.db.execute("SELECT count(*) FROM sales WHERE source=?",(value["source"],)).fetchone()[0]
            value["payments_count"]=self.db.execute("SELECT count(*) FROM sale_payments p JOIN sales s USING(sale_id) WHERE s.source=?",(value["source"],)).fetchone()[0]
        return list(states.values())

    def sales(self):
        result=[]
        for row in self.db.execute("SELECT * FROM sales ORDER BY sold_at DESC"):
            item=dict(row);item["lines"]=[dict(x) for x in self.db.execute("SELECT * FROM sale_lines WHERE sale_id=?",(row["sale_id"],))];result.append(item)
        return result

    def unmatched(self):
        return [dict(r) for r in self.db.execute("SELECT s.source,s.external_sale_id,s.sold_at,l.* FROM sale_lines l JOIN sales s USING(sale_id) WHERE mapping_status IN ('UNMATCHED','AMBIGUOUS') ORDER BY s.sold_at DESC")]
