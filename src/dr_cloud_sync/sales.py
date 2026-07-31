"""Append-only, source-neutral Sales Ledger and deterministic analytics.

This module never writes to catalogue or stock tables.  Unknown commercial facts
remain ``None``: in particular, a missing amount is not revenue equal to zero.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4


class SaleSource(StrEnum):
    SHOPCAISSE="SHOPCAISSE"; PRESTASHOP="PRESTASHOP"; MANUAL="MANUAL"; IMPORT="IMPORT"


class SaleKind(StrEnum):
    SALE="SALE"; REFUND="REFUND"; RETURN="RETURN"; ADJUSTMENT="ADJUSTMENT"; CANCELLATION="CANCELLATION"


class Freshness(StrEnum):
    FRESH="FRESH"; STALE="STALE"; UNAVAILABLE="UNAVAILABLE"; ERROR="ERROR"


class SalesSourcePort(Protocol):
    @property
    def configured(self) -> bool: ...
    def fetch(self, since: datetime | None = None) -> Sequence[Mapping[str, Any]]: ...


class SocialAnalyticsPort(Protocol):
    @property
    def configured(self) -> bool: ...
    def fetch(self, post_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SaleEvent:
    source: str
    external_sale_id: str
    external_line_id: str
    sold_at: str
    timezone: str
    kind: str
    product_key: str | None
    quantity: Decimal
    idempotency_key: str
    unit_price_ttc: Decimal | None = None
    unit_price_ht: Decimal | None = None
    line_total_ttc: Decimal | None = None
    line_total_ht: Decimal | None = None
    cost_basis: Decimal | None = None
    currency: str | None = None
    channel: str | None = None
    location: str | None = None
    raw_reference: str | None = None
    source_updated_at: str | None = None


SCHEMA="""
CREATE TABLE IF NOT EXISTS sale_events(
 sale_event_id TEXT PRIMARY KEY, source TEXT NOT NULL, external_sale_id TEXT NOT NULL,
 external_line_id TEXT NOT NULL, sold_at TEXT NOT NULL, timezone TEXT NOT NULL,
 event_kind TEXT NOT NULL, product_key TEXT, match_status TEXT NOT NULL,
 quantity TEXT NOT NULL, unit_price_ttc TEXT, unit_price_ht TEXT, line_total_ttc TEXT,
 line_total_ht TEXT, cost_basis TEXT, currency TEXT, channel TEXT, location TEXT,
 raw_reference TEXT, imported_at TEXT NOT NULL, source_updated_at TEXT,
 idempotency_key TEXT NOT NULL UNIQUE, import_batch_id TEXT,
 UNIQUE(source,external_sale_id,external_line_id,event_kind));
CREATE TABLE IF NOT EXISTS sales_import_batches(
 batch_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, previewed_at TEXT NOT NULL,
 applied_at TEXT, status TEXT NOT NULL, report_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sales_audit(
 audit_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,occurred_at TEXT NOT NULL,
 actor TEXT NOT NULL,details_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sale_events_sold_at ON sale_events(sold_at);
CREATE INDEX IF NOT EXISTS ix_sale_events_product_date ON sale_events(product_key,sold_at);
CREATE INDEX IF NOT EXISTS ix_sale_events_source ON sale_events(source);
CREATE INDEX IF NOT EXISTS ix_sale_events_external ON sale_events(source,external_sale_id);
CREATE INDEX IF NOT EXISTS ix_sale_events_idempotency ON sale_events(idempotency_key);
"""

CSV_COLUMNS=("source","external_sale_id","external_line_id","sold_at","timezone","event_kind",
             "product_key","ean","reference","shopcaisse_item_id","quantity","unit_price_ttc",
             "unit_price_ht","line_total_ttc","line_total_ht","cost_basis","currency","channel",
             "location","raw_reference","source_updated_at")


def _utc(value: str) -> datetime:
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None: raise ValueError("sold_at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any, *, required: bool=False) -> Decimal | None:
    if value is None or str(value).strip()=="":
        if required: raise ValueError("required decimal is missing")
        return None
    try: return Decimal(str(value))
    except InvalidOperation as exc: raise ValueError(f"invalid decimal: {value}") from exc


class SalesLedger:
    """Transactional ledger, exact product matching, import and aggregation."""
    def __init__(self,path: Path,catalogue: Any,*,stale_after_hours: int=48):
        self.path=Path(path); self.catalogue=catalogue; self.stale_after_hours=stale_after_hours
        self.db=sqlite3.connect(path,check_same_thread=False); self.db.row_factory=sqlite3.Row
        with self.db:self.db.executescript(SCHEMA)

    @staticmethod
    def key(source: str, sale: str, line: str, kind: str) -> str:
        return hashlib.sha256("|".join((source,sale,line,kind)).encode()).hexdigest()

    def _resolve(self,row: Mapping[str,Any]) -> str | None:
        candidates=[]
        for product in self.catalogue.all():
            if row.get("product_key") and product.drcloud_product_key==row["product_key"]: candidates.append(product.drcloud_product_key)
            elif row.get("ean") and getattr(product,"ean",None)==row["ean"]: candidates.append(product.drcloud_product_key)
            elif row.get("reference") and getattr(product,"reference",None)==row["reference"]: candidates.append(product.drcloud_product_key)
            elif row.get("shopcaisse_item_id") and str(getattr(product,"shopcaisse_item_id",''))==str(row["shopcaisse_item_id"]): candidates.append(product.drcloud_product_key)
        unique=set(candidates)
        return next(iter(unique)) if len(unique)==1 else None

    def resolve_candidates(self,row: Mapping[str,Any]) -> list[str]:
        """Return exact candidates; ambiguity is data, never an automatic match."""
        keys=[]
        for product in self.catalogue.all():
            checks=(
                row.get("product_key") and product.drcloud_product_key==row["product_key"],
                row.get("prestashop_combination_id") and str(getattr(product,"combination_id",''))==str(row["prestashop_combination_id"]),
                row.get("shopcaisse_item_id") and str(getattr(product,"shopcaisse_item_id",''))==str(row["shopcaisse_item_id"]),
                row.get("ean") and getattr(product,"ean",None)==row["ean"],
                row.get("reference") and getattr(product,"reference",None)==row["reference"],
            )
            if any(checks): keys.append(product.drcloud_product_key)
        return sorted(set(keys))

    def append(self,event: SaleEvent,*,match_status: str="MATCHED",batch_id: str|None=None) -> bool:
        """Append one normalized event to the existing ledger (no stock side effect)."""
        values=asdict(event)
        cursor=self.db.execute("""INSERT OR IGNORE INTO sale_events VALUES(
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (f"sale:{uuid4()}",event.source,event.external_sale_id,event.external_line_id,event.sold_at,event.timezone,
               event.kind,event.product_key,match_status,str(event.quantity),
               *[str(values[x]) if values[x] is not None else None for x in ("unit_price_ttc","unit_price_ht","line_total_ttc","line_total_ht","cost_basis")],
               event.currency,event.channel,event.location,event.raw_reference,_now(),event.source_updated_at,event.idempotency_key,batch_id))
        return bool(cursor.rowcount)

    def list_events(self,limit: int=200) -> list[dict[str,Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM sale_events ORDER BY sold_at DESC LIMIT ?",(limit,))]

    def _parse(self,row: Mapping[str,Any],number: int) -> SaleEvent:
        source=SaleSource(str(row.get("source") or "IMPORT").upper()).value
        kind=SaleKind(str(row.get("event_kind") or "SALE").upper()).value
        sale=str(row.get("external_sale_id") or "").strip(); line=str(row.get("external_line_id") or "").strip()
        if not sale or not line: raise ValueError("external_sale_id and external_line_id are required")
        sold=_utc(str(row.get("sold_at") or "")); quantity=_decimal(row.get("quantity"),required=True)
        if quantity is None or quantity <= 0: raise ValueError("quantity must be positive; kind carries the sign")
        currency=str(row.get("currency") or "").upper() or None
        amounts={name:_decimal(row.get(name)) for name in ("unit_price_ttc","unit_price_ht","line_total_ttc","line_total_ht","cost_basis")}
        if any(value is not None for value in amounts.values()) and not currency: raise ValueError("currency is required when an amount is supplied")
        return SaleEvent(source,sale,line,sold.isoformat(),str(row.get("timezone") or "UTC"),kind,
            self._resolve(row),quantity,self.key(source,sale,line,kind),currency=currency,
            channel=str(row.get("channel") or "") or None,location=str(row.get("location") or "") or None,
            raw_reference=str(row.get("raw_reference") or "") or None,
            source_updated_at=_utc(str(row["source_updated_at"])).isoformat() if row.get("source_updated_at") else None,**amounts)

    def preview_csv(self,content: str,actor: str="authenticated") -> dict[str,Any]:
        digest=hashlib.sha256(content.encode()).hexdigest(); parsed=list(csv.DictReader(io.StringIO(content)))
        valid=[]; errors=[]; duplicate=0; seen=set()
        for number,row in enumerate(parsed,2):
            try:
                event=self._parse(row,number)
                exists=self.db.execute("SELECT 1 FROM sale_events WHERE idempotency_key=?",(event.idempotency_key,)).fetchone()
                if event.idempotency_key in seen or exists: duplicate+=1
                else: valid.append(event);seen.add(event.idempotency_key)
            except (ValueError,KeyError) as exc: errors.append({"line":number,"error":str(exc)})
        dates=[e.sold_at for e in valid]; matched=sum(e.product_key is not None for e in valid)
        report={"rows":len(parsed),"valid":len(valid),"invalid":len(errors),"duplicates":duplicate,
                "matched":matched,"unmatched":len(valid)-matched,"errors":errors,
                "date_from":min(dates) if dates else None,"date_to":max(dates) if dates else None,
                "content_hash":digest,"can_apply":bool(valid) and not errors}
        batch=f"sales-import:{uuid4()}"
        with self.db:
            self.db.execute("INSERT INTO sales_import_batches VALUES(?,?,?,NULL,'PREVIEWED',?)",(batch,digest,_now(),json.dumps(report)))
            self._audit("SALES_IMPORT_PREVIEWED",actor,{"batch_id":batch,**report})
        return {**report,"batch_id":batch}

    def apply_csv(self,batch_id: str,content: str,actor: str="authenticated") -> dict[str,Any]:
        batch=self.db.execute("SELECT * FROM sales_import_batches WHERE batch_id=?",(batch_id,)).fetchone()
        if not batch or batch["status"]!="PREVIEWED": raise ValueError("a valid, unused preview is required")
        if hashlib.sha256(content.encode()).hexdigest()!=batch["content_hash"]: raise ValueError("CSV differs from preview")
        preview=json.loads(batch["report_json"])
        if not preview["can_apply"]: raise ValueError("preview contains invalid rows")
        events=[]
        for number,row in enumerate(csv.DictReader(io.StringIO(content)),2): events.append(self._parse(row,number))
        inserted=duplicates=0
        with self.db:
            for event in events:
                values=asdict(event)
                cursor=self.db.execute("""INSERT OR IGNORE INTO sale_events VALUES(
                  ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (f"sale:{uuid4()}",event.source,event.external_sale_id,event.external_line_id,event.sold_at,event.timezone,
                   event.kind,event.product_key,"MATCHED" if event.product_key else "UNMATCHED",str(event.quantity),
                   *[str(values[x]) if values[x] is not None else None for x in ("unit_price_ttc","unit_price_ht","line_total_ttc","line_total_ht","cost_basis")],
                   event.currency,event.channel,event.location,event.raw_reference,_now(),event.source_updated_at,event.idempotency_key,batch_id))
                inserted+=cursor.rowcount;duplicates+=cursor.rowcount==0
            result={"batch_id":batch_id,"inserted":inserted,"duplicates":duplicates,"unmatched":sum(e.product_key is None for e in events)}
            self.db.execute("UPDATE sales_import_batches SET status='APPLIED',applied_at=?,report_json=? WHERE batch_id=?",(_now(),json.dumps(result),batch_id))
            self._audit("SALES_IMPORTED",actor,result)
        return result

    def _audit(self,event: str,actor: str,details: Mapping[str,Any]) -> None:
        self.db.execute("INSERT INTO sales_audit VALUES(?,?,?,?,?)",(f"sales-audit:{uuid4()}",event,_now(),actor,json.dumps(dict(details),sort_keys=True)))

    @staticmethod
    def _effect(kind: str) -> Decimal:
        return Decimal("1") if kind in {"SALE","ADJUSTMENT"} else Decimal("-1")

    def metrics(self,product_key: str|None,days: int,*,as_of: datetime|None=None,previous: bool=False) -> dict[str,Any]:
        end=(as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if previous:end-=timedelta(days=days)
        start=end-timedelta(days=days); args=[start.isoformat(),end.isoformat()]
        where="sold_at>=? AND sold_at<?"
        if product_key is not None:where+=" AND product_key=?";args.append(product_key)
        rows=self.db.execute(f"SELECT * FROM sale_events WHERE {where}",args).fetchall()
        units=sum((Decimal(r["quantity"])*self._effect(r["event_kind"]) for r in rows),Decimal(0))
        def total(field: str):
            if not rows or any(r[field] is None for r in rows): return None
            return sum((Decimal(r[field])*self._effect(r["event_kind"]) for r in rows),Decimal(0))
        ttc=total("line_total_ttc");ht=total("line_total_ht")
        sales=[r for r in rows if r["event_kind"]=="SALE"]
        return {"available":bool(rows),"window":{"start":start.isoformat(),"end":end.isoformat()},"units_sold":float(units),
                "transactions":len({(r["source"],r["external_sale_id"]) for r in rows}),
                "revenue_ttc":float(ttc) if ttc is not None else None,"revenue_ht":float(ht) if ht is not None else None,
                "average_unit_price_ttc":float(ttc/units) if ttc is not None and units else None,
                "sales_velocity":float(units/Decimal(days)),"last_sale_at":max((r["sold_at"] for r in sales),default=None),
                "gross_margin":None,"gross_margin_available":False}

    def product_metrics(self,key: str,*,as_of: datetime|None=None) -> dict[str,Any]:
        current=self.metrics(key,7,as_of=as_of); previous=self.metrics(key,7,as_of=as_of,previous=True)
        prior=previous["units_sold"]
        growth=(current["units_sold"]-prior)/prior if previous["available"] and prior!=0 else None
        return {"product_key":key,"last_7_days":current,"previous_7_days":previous,"last_30_days":self.metrics(key,30,as_of=as_of),"growth_rate":growth}

    def status(self,*,as_of: datetime|None=None) -> dict[str,Any]:
        now_at=as_of or datetime.now(timezone.utc); row=self.db.execute("SELECT COUNT(*) n,MIN(sold_at) first,MAX(sold_at) last,MAX(imported_at) imported FROM sale_events").fetchone()
        sources=[dict(r) for r in self.db.execute("SELECT source,COUNT(*) events,MAX(imported_at) last_import FROM sale_events GROUP BY source")]
        freshness=Freshness.UNAVAILABLE
        if row["imported"]:freshness=Freshness.FRESH if now_at-_utc(row["imported"])<=timedelta(hours=self.stale_after_hours) else Freshness.STALE
        return {"events":row["n"],"period":{"from":row["first"],"to":row["last"]},"last_import":row["imported"],"sources":sources,
                "unmatched":self.db.execute("SELECT COUNT(*) FROM sale_events WHERE match_status='UNMATCHED'").fetchone()[0],
                "duplicates_blocked":self.db.execute("SELECT COALESCE(SUM(json_extract(report_json,'$.duplicates')),0) FROM sales_import_batches WHERE status='APPLIED'").fetchone()[0],
                "freshness":freshness.value,"stale_after_hours":self.stale_after_hours,
                "connectors":{"SHOPCAISSE":"NOT_CONFIGURED","PRESTASHOP":"NOT_CONFIGURED","MANUAL_CSV":"AVAILABLE"}}

    def analytics(self,*,as_of: datetime|None=None) -> dict[str,Any]:
        products=[]
        for product in self.catalogue.all():
            metric=self.product_metrics(product.drcloud_product_key,as_of=as_of)
            if metric["last_7_days"]["available"] or metric["previous_7_days"]["available"]: products.append({"name":product.name,**metric})
        products.sort(key=lambda x:x["last_7_days"]["units_sold"],reverse=True)
        return {"status":self.status(as_of=as_of),"totals":{"last_7_days":self.metrics(None,7,as_of=as_of),"last_30_days":self.metrics(None,30,as_of=as_of)},"products":products}


def _now() -> str:return datetime.now(timezone.utc).isoformat()


class SocialAnalyticsService:
    """Stores provider-returned nullable metrics as idempotent current snapshots."""
    FIELDS=("reach","impressions","views","clicks","engagement","conversions")
    def __init__(self,db: sqlite3.Connection,provider: SocialAnalyticsPort|None=None):
        self.db=db;self.provider=provider
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS social_analytics_snapshots(
              post_id TEXT PRIMARY KEY,source TEXT NOT NULL,fetched_at TEXT NOT NULL,
              reach INTEGER,impressions INTEGER,views INTEGER,clicks INTEGER,
              engagement REAL,conversions INTEGER,raw_json TEXT NOT NULL)""")

    def refresh(self,post_id: str,source: str,actor: str="job:social-analytics") -> dict[str,Any]:
        if not self.provider or not self.provider.configured: raise RuntimeError("analytics provider disabled")
        raw=dict(self.provider.fetch(post_id)); unknown=set(raw)-set(self.FIELDS)
        # Unknown provider fields remain in raw_json, but never become invented domain metrics.
        values={field:raw.get(field) for field in self.FIELDS}
        stamp=_now()
        with self.db:
            self.db.execute("""INSERT INTO social_analytics_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(post_id) DO UPDATE SET source=excluded.source,fetched_at=excluded.fetched_at,
              reach=excluded.reach,impressions=excluded.impressions,views=excluded.views,
              clicks=excluded.clicks,engagement=excluded.engagement,conversions=excluded.conversions,
              raw_json=excluded.raw_json""",(post_id,source,stamp,*(values[x] for x in self.FIELDS),json.dumps(raw)))
        return {"post_id":post_id,"source":source,"fetched_at":stamp,**values,"ignored_fields":sorted(unknown)}

    def summary(self) -> dict[str,Any]:
        rows=[dict(r) for r in self.db.execute("SELECT * FROM social_analytics_snapshots ORDER BY fetched_at DESC")]
        averages={}
        for field in self.FIELDS:
            known=[r[field] for r in rows if r[field] is not None]
            averages[field]=sum(known)/len(known) if known else None
        return {"posts":rows,"averages":averages,"configured":bool(self.provider and self.provider.configured)}
