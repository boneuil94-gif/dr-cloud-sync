"""Official SumUp read-only payments adapter and settlement ledgers.

Only documented GET resources are used.  The adapter deliberately exposes no
generic request method and consequently cannot create payments or refunds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib, json, sqlite3, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SumUpError(RuntimeError):
    def __init__(self, message, *, retryable=False, diagnostic=None):
        super().__init__(message); self.retryable=retryable; self.diagnostic=diagnostic or {}


@dataclass(frozen=True)
class SumUpPage:
    rows: tuple[dict, ...]
    next_cursor: str | None


def _decimal(value): return str(Decimal(str(value or 0)))
def _cursor(value):
    if not value: return {}
    try: return json.loads(value)
    except (TypeError,json.JSONDecodeError): raise SumUpError("Curseur SumUp invalide",diagnostic={"stage":"pagination","category":"VALIDATION"})


class SumUpProvider:
    """Minimal API-key client for SumUp merchant transaction and payout reads."""
    BASE_URL="https://api.sumup.com"
    def __init__(self,merchant_code,secret_ref,secrets,*,base_url=None,opener=urlopen,
                 timeout=8,page_size=100,retries=3,sleep=time.sleep,overlap_seconds=3600):
        self.merchant_code=str(merchant_code or "").strip();self.secret_ref=secret_ref;self.secrets=secrets
        self.base_url=(base_url or self.BASE_URL).rstrip("/");self.opener=opener;self.timeout=timeout
        self.page_size=min(100,max(1,int(page_size)));self.retries=max(1,int(retries));self.sleep=sleep
        self.overlap_seconds=max(0,int(overlap_seconds))
    @property
    def configured(self): return bool(self.merchant_code and self.secret_ref and self.secrets.get(self.secret_ref))
    def _path(self,resource): return f"/v2.1/merchants/{self.merchant_code}/{resource}" if resource.startswith("transactions") else f"/v1.0/merchants/{self.merchant_code}/{resource}"
    def _get(self,path,params=None,*,stage):
        key=self.secrets.get(self.secret_ref) if self.secret_ref else None
        if not key or not self.merchant_code: raise SumUpError("SumUp n'est pas configuré",diagnostic={"stage":"authentication","category":"CONFIGURATION","endpoint_path":path})
        url=self.base_url+path+("?"+urlencode(params,doseq=True) if params else "")
        request=Request(url,method="GET",headers={"Authorization":f"Bearer {key}","Accept":"application/json"})
        for attempt in range(self.retries):
            try:
                with self.opener(request,timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                retryable=exc.code==429 or 500<=exc.code<600
                diagnostic={"stage":stage,"http_status":exc.code,"endpoint_path":path,"category":"AUTH" if exc.code in (401,403) else "HTTP"}
                if exc.code in (401,403): raise SumUpError(f"Authentification SumUp refusée (HTTP {exc.code})",diagnostic=diagnostic) from exc
                if not retryable or attempt+1==self.retries: raise SumUpError(f"Erreur SumUp HTTP {exc.code}",retryable=retryable,diagnostic=diagnostic) from exc
                self.sleep(float(exc.headers.get("Retry-After") or min(30,2**attempt)))
            except (URLError,TimeoutError) as exc:
                if attempt+1==self.retries: raise SumUpError("Délai réseau SumUp dépassé",retryable=True,diagnostic={"stage":stage,"category":"TIMEOUT","endpoint_path":path}) from exc
                self.sleep(min(30,2**attempt))
            except (UnicodeDecodeError,json.JSONDecodeError) as exc:
                raise SumUpError("Réponse JSON SumUp invalide",diagnostic={"stage":"parsing","category":"PARSING","endpoint_path":path}) from exc
        raise AssertionError("unreachable")
    def health(self):
        if not self.configured:return {"status":"NOT_CONFIGURED"}
        # History is the documented merchant-scoped read that validates both key and merchant.
        self._get(self._path("transactions/history"),{"limit":1},stage="authentication")
        return {"status":"CONNECTED","merchant_code":self.merchant_code}
    def transactions(self,cursor=None):
        state=_cursor(cursor); params={"limit":self.page_size,"order":"ascending"}
        if state.get("next"): params["newest_time"]=state["next"]
        elif state.get("watermark"):
            point=datetime.fromisoformat(state["watermark"].replace("Z","+00:00"))-timedelta(seconds=self.overlap_seconds);params["oldest_time"]=point.isoformat()
        payload=self._get(self._path("transactions/history"),params,stage="transaction_history")
        rows=payload.get("items",payload.get("transactions",payload if isinstance(payload,list) else []))
        if not isinstance(rows,list): raise SumUpError("Contrat transactions SumUp invalide",diagnostic={"stage":"parsing","category":"PARSING"})
        enriched=[]
        for row in rows:
            # Detail supplies events/refunds/chargebacks/payout events absent from some history rows.
            identifier=row.get("id") or row.get("transaction_code")
            if identifier:
                detail=self._get(self._path("transactions"),{"id":identifier},stage="transaction_detail")
                if isinstance(detail,dict): row={**row,**detail}
            enriched.append(row)
        rows=enriched
        watermark=max((str(x.get("timestamp") or "") for x in rows),default=state.get("watermark"))
        next_value=payload.get("next") or payload.get("next_page")
        return SumUpPage(tuple(rows),json.dumps({"next":next_value,"watermark":watermark}) if next_value else json.dumps({"watermark":watermark}) if watermark else None)
    def payouts(self,cursor=None):
        state=_cursor(cursor);offset=int(state.get("offset",0));payload=self._get(self._path("payouts"),{"limit":self.page_size,"offset":offset},stage="payouts")
        rows=payload.get("items",payload.get("payouts",payload if isinstance(payload,list) else []))
        if not isinstance(rows,list): raise SumUpError("Contrat payouts SumUp invalide",diagnostic={"stage":"parsing","category":"PARSING"})
        more=bool(payload.get("next") or payload.get("has_more") or len(rows)==self.page_size)
        return SumUpPage(tuple(rows),json.dumps({"offset":offset+len(rows)}) if more and rows else None)


SCHEMA="""
CREATE TABLE IF NOT EXISTS sumup_transactions(sumup_transaction_id TEXT PRIMARY KEY,transaction_code TEXT,amount TEXT NOT NULL,currency TEXT NOT NULL,timestamp TEXT NOT NULL,status TEXT,payment_type TEXT,entry_mode TEXT,vat_amount TEXT,tip_amount TEXT,foreign_transaction_id TEXT,client_transaction_id TEXT,fee TEXT NOT NULL,events_json TEXT NOT NULL,raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sumup_transaction_code ON sumup_transactions(transaction_code) WHERE transaction_code IS NOT NULL;
CREATE TABLE IF NOT EXISTS sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,transaction_code TEXT,deductions_json TEXT NOT NULL,raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_settlements(settlement_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,payout_id TEXT NOT NULL,amount TEXT,currency TEXT,created_at TEXT NOT NULL,UNIQUE(sumup_transaction_id,payout_id));
"""
def _safe_raw(row): return {k:v for k,v in row.items() if not any(s in k.lower() for s in ("authorization","token","secret","api_key","key"))}


class SumUpTransactionLedger:
    def __init__(self,path:Path|sqlite3.Connection):
        self.db=path if isinstance(path,sqlite3.Connection) else sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.executescript(SCHEMA);self.db.commit()
    def import_page(self,page):
        inserted=0; stamp=datetime.now(timezone.utc).isoformat()
        with self.db:
            for row in page.rows:
                transaction_id=str(row.get("id") or row.get("transaction_id") or row.get("transaction_code") or hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest())
                exists=self.db.execute("SELECT 1 FROM sumup_transactions WHERE sumup_transaction_id=?",(transaction_id,)).fetchone();inserted+=not bool(exists)
                events=row.get("events") or []; fee=row.get("fee_amount",row.get("fee",0))
                self.db.execute("""INSERT INTO sumup_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sumup_transaction_id) DO UPDATE SET transaction_code=excluded.transaction_code,amount=excluded.amount,status=excluded.status,fee=excluded.fee,events_json=excluded.events_json,raw_json=excluded.raw_json,imported_at=excluded.imported_at""",(transaction_id,row.get("transaction_code"),_decimal(row.get("amount")),row.get("currency") or "EUR",str(row.get("timestamp") or row.get("date") or stamp),row.get("simple_status") or row.get("status"),row.get("payment_type"),row.get("entry_mode"),_decimal(row.get("vat_amount")),_decimal(row.get("tip_amount")),row.get("foreign_transaction_id"),row.get("client_transaction_id"),_decimal(fee),json.dumps(events),json.dumps(_safe_raw(row)),stamp))
        return {"rows_imported":inserted,"duplicates":len(page.rows)-inserted,"cursor":page.next_cursor}
    def sync(self,provider,cursor=None):
        total=duplicates=0
        while True:
            page=provider.transactions(cursor);result=self.import_page(page);total+=result["rows_imported"];duplicates+=result["duplicates"];cursor=page.next_cursor
            if not cursor or not _cursor(cursor).get("next"):break
        return {"rows_imported":total,"duplicates":duplicates,"cursor":cursor}
    def rows(self): return [dict(r) for r in self.db.execute("SELECT * FROM sumup_transactions ORDER BY timestamp DESC")]


class PaymentSettlementLedger:
    def __init__(self,path:Path|sqlite3.Connection):
        self.db=path if isinstance(path,sqlite3.Connection) else sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.executescript(SCHEMA);self.db.commit()
    def import_page(self,page):
        inserted=0;stamp=datetime.now(timezone.utc).isoformat()
        with self.db:
            for row in page.rows:
                pid=str(row.get("id") or row.get("payout_id"));exists=self.db.execute("SELECT 1 FROM sumup_payouts WHERE payout_id=?",(pid,)).fetchone();inserted+=not bool(exists)
                deductions=row.get("deductions") or row.get("transactions") or []
                self.db.execute("""INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(payout_id) DO UPDATE SET amount=excluded.amount,fee=excluded.fee,status=excluded.status,deductions_json=excluded.deductions_json,raw_json=excluded.raw_json,imported_at=excluded.imported_at""",(pid,row.get("type"),str(row.get("date") or row.get("timestamp") or stamp),_decimal(row.get("amount")),row.get("currency") or "EUR",_decimal(row.get("fee")),row.get("status"),row.get("reference"),row.get("transaction_code"),json.dumps(deductions),json.dumps(_safe_raw(row)),stamp))
                codes={str(x.get("transaction_code") or x.get("id")) for x in deductions if isinstance(x,dict)}
                if row.get("transaction_code"):codes.add(str(row["transaction_code"]))
                for code in codes:
                    tx=self.db.execute("SELECT sumup_transaction_id,amount,currency FROM sumup_transactions WHERE transaction_code=? OR sumup_transaction_id=?",(code,code)).fetchone()
                    if tx:self.db.execute("INSERT OR IGNORE INTO payment_settlements VALUES(?,?,?,?,?,?)",(f"sumup:{tx['sumup_transaction_id']}:{pid}",tx["sumup_transaction_id"],pid,tx["amount"],tx["currency"],stamp))
        return {"rows_imported":inserted,"duplicates":len(page.rows)-inserted,"cursor":page.next_cursor}
    def sync(self,provider,cursor=None):
        total=duplicates=0
        while True:
            page=provider.payouts(cursor);result=self.import_page(page);total+=result["rows_imported"];duplicates+=result["duplicates"];cursor=page.next_cursor
            if cursor is None:break
        return {"rows_imported":total,"duplicates":duplicates,"cursor":cursor}
    def rows(self): return [dict(r) for r in self.db.execute("SELECT * FROM sumup_payouts ORDER BY payout_date DESC")]
    def reconcile(self):
        # Re-run linking after transactions/payouts arrive in either order.
        pages=SumUpPage(tuple({**dict(r),"deductions":json.loads(r["deductions_json"])} for r in self.db.execute("SELECT * FROM sumup_payouts")),None);self.import_page(pages)
        return self.db.execute("SELECT count(*) FROM payment_settlements").fetchone()[0]
