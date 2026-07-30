"""Canonical, local and read-only sales ledger.

External systems are sources only: this module never writes to them or to stock.
Money is persisted as decimal text so SQLite/Python never introduce binary floats.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
import sqlite3
import uuid


class SaleSource(StrEnum):
    PRESTASHOP = "PRESTASHOP"
    SHOPCAISSE = "SHOPCAISSE"


class SaleChannel(StrEnum):
    ONLINE = "ONLINE"
    STORE = "STORE"
    UNKNOWN = "UNKNOWN"


class SaleStatus(StrEnum):
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    PENDING = "PENDING"


MONEY = Decimal("0.01")


def money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Montant invalide") from exc


@dataclass(frozen=True)
class SaleLine:
    sale_line_id: str
    sale_id: str
    source_line_id: str
    product_key: str | None
    source_product_reference: str
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    line_total: Decimal
    tax_amount: Decimal | None = None
    mapping_status: str = "MAPPED"


@dataclass(frozen=True)
class Sale:
    sale_id: str
    source: SaleSource
    source_sale_id: str
    channel: SaleChannel
    occurred_at: str
    status: SaleStatus
    currency: str
    gross_total: Decimal
    discount_total: Decimal
    tax_total: Decimal | None
    net_total: Decimal
    refund_total: Decimal
    customer_reference: str | None
    imported_at: str
    job_id: str | None
    created_at: str


class SQLiteSalesRepository:
    """Transactional ledger; external identity is immutable and idempotent."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sales(
          sale_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_sale_id TEXT NOT NULL,
          channel TEXT NOT NULL, occurred_at TEXT NOT NULL, status TEXT NOT NULL,
          currency TEXT NOT NULL, gross_total TEXT NOT NULL, discount_total TEXT NOT NULL,
          tax_total TEXT, net_total TEXT NOT NULL, refund_total TEXT NOT NULL DEFAULT '0.00',
          customer_reference TEXT, imported_at TEXT NOT NULL, job_id TEXT, created_at TEXT NOT NULL,
          UNIQUE(source, source_sale_id));
        CREATE TABLE IF NOT EXISTS sale_lines(
          sale_line_id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES sales(sale_id),
          source_line_id TEXT NOT NULL, product_key TEXT, source_product_reference TEXT NOT NULL,
          quantity INTEGER NOT NULL CHECK(quantity > 0), unit_price TEXT NOT NULL,
          discount_amount TEXT NOT NULL, line_total TEXT NOT NULL, tax_amount TEXT,
          mapping_status TEXT NOT NULL, UNIQUE(sale_id, source_line_id));
        CREATE TABLE IF NOT EXISTS sales_source_state(
          source TEXT PRIMARY KEY, quality TEXT NOT NULL, last_success_at TEXT,
          cursor TEXT, message TEXT);
        CREATE INDEX IF NOT EXISTS idx_sales_occurred ON sales(occurred_at);
        CREATE INDEX IF NOT EXISTS idx_sales_status ON sales(status);
        CREATE INDEX IF NOT EXISTS idx_sales_channel ON sales(channel);
        CREATE INDEX IF NOT EXISTS idx_sale_lines_product ON sale_lines(product_key);
        """)
        self.db.commit()

    def import_batch(self, entries: list[tuple[Sale, list[SaleLine]]]) -> dict:
        created = 0
        with self.db:
            for sale, lines in entries:
                exists = self.db.execute("SELECT sale_id FROM sales WHERE source=? AND source_sale_id=?",(sale.source,sale.source_sale_id)).fetchone()
                if exists:
                    continue
                values=asdict(sale); values={k:(v.value if isinstance(v,StrEnum) else str(v) if isinstance(v,Decimal) else v) for k,v in values.items()}
                self.db.execute("INSERT INTO sales VALUES(:sale_id,:source,:source_sale_id,:channel,:occurred_at,:status,:currency,:gross_total,:discount_total,:tax_total,:net_total,:refund_total,:customer_reference,:imported_at,:job_id,:created_at)",values)
                for line in lines:
                    row=asdict(line); row={k:(str(v) if isinstance(v,Decimal) else v) for k,v in row.items()}
                    self.db.execute("INSERT INTO sale_lines VALUES(:sale_line_id,:sale_id,:source_line_id,:product_key,:source_product_reference,:quantity,:unit_price,:discount_amount,:line_total,:tax_amount,:mapping_status)",row)
                created += 1
        return {"created":created,"unchanged":len(entries)-created}

    def get(self, sale_id):
        row=self.db.execute("SELECT * FROM sales WHERE sale_id=?",(sale_id,)).fetchone()
        return self._sale(row) if row else None

    def lines_for_sale(self, sale_id):
        return [self._line(r) for r in self.db.execute("SELECT * FROM sale_lines WHERE sale_id=? ORDER BY rowid",(sale_id,))]

    def list(self, *, start=None, end=None, channel=None, search="", page=1, per_page=25):
        where,args=self._filters(start,end,channel); term=f"%{search}%"
        if search: where.append("(source_sale_id LIKE ? OR source LIKE ? OR sale_id IN (SELECT sale_id FROM sale_lines WHERE source_product_reference LIKE ? OR product_key LIKE ?))"); args += [term]*4
        clause=" WHERE "+" AND ".join(where) if where else ""
        total=self.db.execute("SELECT COUNT(*) FROM sales"+clause,args).fetchone()[0]
        rows=self.db.execute("SELECT * FROM sales"+clause+" ORDER BY occurred_at DESC LIMIT ? OFFSET ?",args+[per_page,(page-1)*per_page])
        return [self._sale(r) for r in rows], total

    def sales_between(self,start,end,channel=None): return self.list(start=start,end=end,channel=channel,per_page=100000)[0]
    def sales_for_product(self,key,start=None,end=None):
        sql="SELECT DISTINCT s.* FROM sales s JOIN sale_lines l ON l.sale_id=s.sale_id WHERE l.product_key=?"; args=[key]
        if start: sql+=" AND s.occurred_at>=?"; args.append(start)
        if end: sql+=" AND s.occurred_at<?"; args.append(end)
        return [self._sale(r) for r in self.db.execute(sql+" ORDER BY occurred_at DESC",args)]

    def source_states(self): return [dict(r) for r in self.db.execute("SELECT * FROM sales_source_state ORDER BY source")]
    def set_source_state(self,source,quality,last_success_at=None,cursor=None,message=None):
        with self.db: self.db.execute("INSERT INTO sales_source_state VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET quality=excluded.quality,last_success_at=excluded.last_success_at,cursor=excluded.cursor,message=excluded.message",(source,quality,last_success_at,cursor,message))

    @staticmethod
    def _filters(start,end,channel):
        where=[]; args=[]
        if start: where.append("occurred_at>=?"); args.append(start)
        if end: where.append("occurred_at<?"); args.append(end)
        if channel: where.append("channel=?"); args.append(channel)
        return where,args

    @staticmethod
    def _sale(row):
        data=dict(row)
        for key in ("gross_total","discount_total","net_total","refund_total"): data[key]=money(data[key])
        data["tax_total"]=money(data["tax_total"]) if data["tax_total"] is not None else None
        data["source"]=SaleSource(data["source"]); data["channel"]=SaleChannel(data["channel"]); data["status"]=SaleStatus(data["status"])
        return Sale(**data)

    @staticmethod
    def _line(row):
        data=dict(row)
        for key in ("unit_price","discount_amount","line_total"): data[key]=money(data[key])
        data["tax_amount"]=money(data["tax_amount"]) if data["tax_amount"] is not None else None
        return SaleLine(**data)


class SalesService:
    COUNTED={SaleStatus.COMPLETED,SaleStatus.PARTIALLY_REFUNDED,SaleStatus.REFUNDED}
    def __init__(self, repository): self.repository=repository

    def normalize(self, source, payload, *, product_mapping=None, job_id=None):
        source=SaleSource(source); sid=str(payload["source_sale_id"]); now=datetime.now(timezone.utc).isoformat()
        occurred=str(payload["occurred_at"]); datetime.fromisoformat(occurred.replace("Z","+00:00"))
        status=SaleStatus(payload.get("status","PENDING")); channel=SaleChannel(payload.get("channel","UNKNOWN"))
        currency=str(payload.get("currency","EUR")).upper()
        if len(currency)!=3: raise ValueError("Devise ISO invalide")
        sale_id="sale:"+str(uuid.uuid5(uuid.NAMESPACE_URL,f"drcloud:{source}:{sid}"))
        lines=[]
        for index,item in enumerate(payload.get("lines",[])):
            quantity=int(item["quantity"])
            if quantity <= 0: raise ValueError("Une ligne de vente doit avoir une quantité positive")
            ref=str(item.get("source_product_reference", "")); key=item.get("product_key") or (product_mapping or {}).get(ref)
            line_source=str(item.get("source_line_id",index)); line_id="saleline:"+str(uuid.uuid5(uuid.NAMESPACE_URL,f"{sale_id}:{line_source}"))
            lines.append(SaleLine(line_id,sale_id,line_source,key,ref,quantity,money(item["unit_price"]),money(item.get("discount_amount",0)),money(item["line_total"]),money(item["tax_amount"]) if item.get("tax_amount") is not None else None,"MAPPED" if key else "UNMAPPED"))
        if not lines: raise ValueError("Vente incomplète: aucune ligne")
        gross=money(payload["gross_total"]); discount=money(payload.get("discount_total",0)); net=money(payload.get("net_total",gross-discount)); refund=money(payload.get("refund_total",net if status==SaleStatus.REFUNDED else 0))
        sale=Sale(sale_id,source,sid,channel,occurred,status,currency,gross,discount,money(payload["tax_total"]) if payload.get("tax_total") is not None else None,net,refund,payload.get("customer_reference"),now,job_id,now)
        return sale,lines

    def import_batch(self, source, payloads, **kwargs):
        normalized=[self.normalize(source,p,**kwargs) for p in payloads]
        return self.repository.import_batch(normalized)

    def statistics(self,start,end,channel=None):
        sales=[s for s in self.repository.sales_between(start,end,channel) if s.status in self.COUNTED]
        currencies={s.currency for s in sales}
        if len(currencies)>1: return {"available":False,"reason":"MULTI_CURRENCY","currencies":sorted(currencies),"sale_count":len(sales)}
        revenue=sum((s.net_total-s.refund_total for s in sales),Decimal(0)); units=sum(sum(l.quantity for l in self.repository.lines_for_sale(s.sale_id)) for s in sales)
        return {"available":bool(sales),"currency":next(iter(currencies),None),"revenue":str(money(revenue)),"sale_count":len(sales),"units":units,"average_basket":str(money(revenue/len(sales))) if sales else None,"period":{"start":start,"end":end},"channel":channel}

    def top_products(self,start,end,channel=None,sort="units",limit=10):
        totals={}
        for sale in self.repository.sales_between(start,end,channel):
            if sale.status not in self.COUNTED: continue
            for line in self.repository.lines_for_sale(sale.sale_id):
                if not line.product_key: continue
                row=totals.setdefault(line.product_key,{"product_key":line.product_key,"units":0,"revenue":Decimal(0),"sales":set()})
                # line_total is the source-observed total after its explicit discount;
                # discount_amount is retained for display and must not be subtracted twice.
                row["units"]+=line.quantity; row["revenue"]+=line.line_total; row["sales"].add(sale.sale_id)
        result=[{"product_key":r["product_key"],"units":r["units"],"revenue":str(money(r["revenue"])),"sales":len(r["sales"])} for r in totals.values()]
        key={"units":"units","revenue":"revenue","sales":"sales"}.get(sort,"units")
        return sorted(result,key=lambda r:Decimal(r[key]) if key=="revenue" else r[key],reverse=True)[:limit]

    def daily_series(self,start,end,channel=None):
        days={}
        for sale in self.repository.sales_between(start,end,channel):
            if sale.status not in self.COUNTED: continue
            day=sale.occurred_at[:10]; row=days.setdefault(day,{"date":day,"revenue":Decimal(0),"sales":0,"units":0})
            row["revenue"]+=sale.net_total-sale.refund_total; row["sales"]+=1; row["units"]+=sum(x.quantity for x in self.repository.lines_for_sale(sale.sale_id))
        return [{**r,"revenue":str(money(r["revenue"]))} for r in sorted(days.values(),key=lambda x:x["date"])]

    def channel_breakdown(self,start,end):
        return {c.value:self.statistics(start,end,c.value) for c in SaleChannel}
