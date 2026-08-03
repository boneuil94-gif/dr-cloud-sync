"""Read-only SumUp ingestion and payment settlement ledger.

The connector intentionally implements only public, documented read endpoints.
Raw provider payloads are retained (after secret filtering) so newly-added SumUp
fields are not lost while typed tables provide stable finance semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .sumup_migrations import migrate_sumup_schema


class SumUpError(RuntimeError):
    def __init__(self, message, *, retryable=False, diagnostic=None):
        super().__init__(message)
        self.retryable = retryable
        self.diagnostic = diagnostic or {}


@dataclass(frozen=True)
class SumUpPage:
    rows: tuple[dict, ...]
    next_cursor: str | None


# Stable domain names used by callers and documentation. Persistence remains
# dictionary based because the upstream response is forward-compatible.
@dataclass(frozen=True)
class SumUpMerchant: merchant_code: str; raw: dict
@dataclass(frozen=True)
class SumUpTransaction: transaction_id: str; raw: dict
@dataclass(frozen=True)
class SumUpTransactionEvent: event_id: str; transaction_id: str; raw: dict
@dataclass(frozen=True)
class SumUpFee: fee_id: str; transaction_id: str; raw: dict
@dataclass(frozen=True)
class SumUpRefund: refund_id: str; transaction_id: str; raw: dict
@dataclass(frozen=True)
class SumUpChargeback: chargeback_id: str; transaction_id: str; raw: dict
@dataclass(frozen=True)
class SumUpReversal: reversal_id: str; transaction_id: str; raw: dict
@dataclass(frozen=True)
class SumUpPayout: payout_id: str; raw: dict
@dataclass(frozen=True)
class SumUpPayoutItem: item_id: str; payout_id: str; raw: dict
@dataclass(frozen=True)
class SumUpReader: reader_id: str; raw: dict


def _decimal(value):
    return str(Decimal(str(value if value is not None else 0)))


def _cursor(value):
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SumUpError("Curseur SumUp invalide", diagnostic={"stage": "pagination", "category": "VALIDATION"}) from exc


def _safe_raw(value):
    """Recursively remove credentials while retaining all business fields."""
    if isinstance(value, dict):
        forbidden = ("authorization", "access_token", "api_key", "secret", "cvv", "cvc", "payment_token", "card_token", "pan")
        return {k: _safe_raw(v) for k, v in value.items() if not any(s in k.lower() for s in forbidden)}
    if isinstance(value, list):
        return [_safe_raw(v) for v in value]
    return value


class SumUpProvider:
    BASE_URL = "https://api.sumup.com"

    def __init__(self, merchant_code, secret_ref, secrets, *, base_url=None, opener=urlopen,
                 timeout=8, page_size=100, retries=3, sleep=time.sleep, overlap_seconds=3600,
                 window_days=31):
        self.merchant_code = str(merchant_code or "").strip()
        self.secret_ref, self.secrets = secret_ref, secrets
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.opener, self.timeout = opener, timeout
        self.page_size = min(100, max(1, int(page_size)))
        self.retries, self.sleep = max(1, int(retries)), sleep
        self.overlap_seconds = max(0, int(overlap_seconds))
        self.window_days = max(1, int(window_days))

    @property
    def configured(self):
        return bool(self.merchant_code and self.secret_ref and self.secrets.get(self.secret_ref))

    def _merchant_path(self, resource, version="v1.0"):
        return f"/{version}/merchants/{self.merchant_code}/{resource}".rstrip("/")

    def _get(self, path, params=None, *, stage):
        key = self.secrets.get(self.secret_ref) if self.secret_ref else None
        if not key or not self.merchant_code:
            raise SumUpError("SumUp n'est pas configuré", diagnostic={"stage": "authentication", "category": "CONFIGURATION", "endpoint_path": path})
        request = Request(self.base_url + path + ("?" + urlencode(params, doseq=True) if params else ""), method="GET",
                          headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        for attempt in range(self.retries):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                diagnostic = {"stage": stage, "http_status": exc.code, "endpoint_path": path,
                              "category": "AUTH" if exc.code in (401, 403) else "NOT_FOUND" if exc.code == 404 else "RATE_LIMIT" if exc.code == 429 else "HTTP"}
                if not retryable or attempt + 1 == self.retries:
                    raise SumUpError(f"Erreur SumUp HTTP {exc.code}", retryable=retryable, diagnostic=diagnostic) from exc
                self.sleep(float(exc.headers.get("Retry-After") or min(30, 2 ** attempt)))
            except (URLError, TimeoutError) as exc:
                if attempt + 1 == self.retries:
                    raise SumUpError("Délai réseau SumUp dépassé", retryable=True, diagnostic={"stage": stage, "category": "TIMEOUT", "endpoint_path": path}) from exc
                self.sleep(min(30, 2 ** attempt))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SumUpError("Réponse JSON SumUp invalide", diagnostic={"stage": "parsing", "category": "PARSING", "endpoint_path": path}) from exc
        raise AssertionError("unreachable")

    def health(self):
        if not self.configured:
            return {"status": "NOT_CONFIGURED"}
        self._get(self._merchant_path("transactions/history", "v2.1"), {"limit": 1}, stage="authentication")
        return {"status": "CONNECTED", "merchant_code": self.merchant_code}

    def merchant(self):
        payload = self._get("/v0.1/me", stage="merchant_profile")
        return payload if isinstance(payload, dict) else {}

    def readers(self):
        payload = self._get(self._merchant_path("readers", "v0.1"), stage="readers")
        rows = payload.get("items", payload.get("readers", [])) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise SumUpError("Contrat readers SumUp invalide", diagnostic={"stage": "parsing", "category": "PARSING"})
        return SumUpPage(tuple(rows), None)

    def transaction_detail(self, identifier):
        return self._get(self._merchant_path("transactions", "v2.1"), {"id": identifier}, stage="transaction_detail")

    def transactions(self, cursor=None, *, start_date=None, end_date=None):
        state = _cursor(cursor)
        params = {"limit": self.page_size, "order": "ascending"}
        if state.get("next"):
            params["newest_time"] = state["next"]
        elif start_date:
            params["oldest_time"] = str(start_date)
            if end_date: params["newest_time"] = str(end_date)
        elif state.get("watermark"):
            point = datetime.fromisoformat(state["watermark"].replace("Z", "+00:00")) - timedelta(seconds=self.overlap_seconds)
            params["oldest_time"] = point.isoformat()
        payload = self._get(self._merchant_path("transactions/history", "v2.1"), params, stage="transaction_history")
        rows = payload.get("items", payload.get("transactions", [])) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise SumUpError("Contrat transactions SumUp invalide", diagnostic={"stage": "parsing", "category": "PARSING"})
        enriched = []
        for original in rows:
            row = dict(original)
            identifier = row.get("id") or row.get("transaction_id") or row.get("transaction_code")
            if identifier:
                detail = self.transaction_detail(identifier)
                if isinstance(detail, dict): row.update(detail)
            enriched.append(row)
        watermark = max((str(x.get("timestamp") or "") for x in enriched), default=state.get("watermark"))
        next_value = payload.get("next") or payload.get("next_page") if isinstance(payload, dict) else None
        result = {"watermark": watermark}
        if next_value: result["next"] = next_value
        return SumUpPage(tuple(enriched), json.dumps(result) if any(result.values()) else None)

    def payouts(self, cursor=None, *, start_date=None, end_date=None):
        """Read payout events in bounded, restartable windows (SumUp requires dates)."""
        state = _cursor(cursor)
        today = date.today()
        start = date.fromisoformat(str(start_date or state.get("start") or (today - timedelta(days=self.window_days))))
        final = date.fromisoformat(str(end_date or state.get("final") or today))
        window_end = min(final, date.fromisoformat(state["window_end"]) if state.get("window_end") else start + timedelta(days=self.window_days))
        offset = int(state.get("offset", 0))
        params = {"start_date": start.isoformat(), "end_date": window_end.isoformat(), "limit": self.page_size, "offset": offset, "format": "json"}
        payload = self._get(self._merchant_path("payouts"), params, stage="payouts")
        rows = payload.get("items", payload.get("payouts", [])) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise SumUpError("Contrat payouts SumUp invalide", diagnostic={"stage": "parsing", "category": "PARSING"})
        more = bool(payload.get("next") or payload.get("has_more") or len(rows) == self.page_size) if isinstance(payload, dict) else len(rows) == self.page_size
        if more and rows:
            nxt = {"start": start.isoformat(), "window_end": window_end.isoformat(), "final": final.isoformat(), "offset": offset + len(rows)}
        elif window_end < final:
            nxt_start = window_end  # intentional overlap at the date boundary
            nxt = {"start": nxt_start.isoformat(), "window_end": min(final, nxt_start + timedelta(days=self.window_days)).isoformat(), "final": final.isoformat(), "offset": 0}
        else:
            nxt = None
        return SumUpPage(tuple(rows), json.dumps(nxt) if nxt else None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sumup_merchants(merchant_code TEXT PRIMARY KEY,legal_name TEXT,trading_name TEXT,country TEXT,currency TEXT,timezone TEXT,status TEXT,payout_settings_json TEXT NOT NULL,raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_transactions(sumup_transaction_id TEXT PRIMARY KEY,transaction_code TEXT,amount TEXT NOT NULL,currency TEXT NOT NULL,timestamp TEXT NOT NULL,status TEXT,simple_status TEXT,payment_type TEXT,entry_mode TEXT,card_type TEXT,terminal_id TEXT,product_summary TEXT,vat_amount TEXT,tip_amount TEXT,refunded_amount TEXT,chargeback_amount TEXT,foreign_transaction_id TEXT,client_transaction_id TEXT,reference TEXT,receipt_url TEXT,fee TEXT NOT NULL,events_json TEXT NOT NULL,raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sumup_transaction_code ON sumup_transactions(transaction_code) WHERE transaction_code IS NOT NULL;
CREATE TABLE IF NOT EXISTS sumup_transaction_events(event_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,event_type TEXT,status TEXT,amount TEXT,currency TEXT,event_at TEXT,payout_id TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_fees(fee_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,fee_type TEXT,amount TEXT NOT NULL,currency TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_refunds(refund_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT,refund_at TEXT,status TEXT,is_partial INTEGER,reason TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_chargebacks(chargeback_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT,chargeback_at TEXT,status TEXT,reason TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_reversals(reversal_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT,reversal_at TEXT,status TEXT,reason TEXT,payout_id TEXT,source TEXT NOT NULL DEFAULT 'transaction_detail',raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_payout_items(item_id TEXT PRIMARY KEY,payout_id TEXT NOT NULL,sumup_transaction_id TEXT,transaction_code TEXT,item_type TEXT,amount TEXT,currency TEXT,occurred_at TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sumup_readers(reader_id TEXT PRIMARY KEY,name TEXT,model TEXT,status TEXT,merchant_code TEXT,store_id TEXT,last_seen TEXT,software_version TEXT,raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_settlements(settlement_id TEXT PRIMARY KEY,sumup_transaction_id TEXT NOT NULL,payout_id TEXT NOT NULL,amount TEXT,currency TEXT,status TEXT NOT NULL DEFAULT 'MATCHED',created_at TEXT NOT NULL,UNIQUE(sumup_transaction_id,payout_id));
"""

PAYOUT_COLUMNS = (
    "payout_id", "type", "payout_date", "amount", "currency", "fee", "status",
    "reference", "start_date", "end_date", "paid_date", "deductions_json", "raw_json", "imported_at",
)


def _payout_values(row, payout_id, stamp):
    return (
        payout_id, row.get("type"),
        str(row.get("payout_date") or row.get("date") or row.get("timestamp") or stamp),
        _decimal(row.get("amount")), row.get("currency") or "EUR", _decimal(row.get("fee")),
        row.get("status"), row.get("reference") or row.get("bank_reference"),
        row.get("start_date"), row.get("end_date"), row.get("paid_date"),
        json.dumps(_safe_raw(row.get("deductions") or [])), json.dumps(_safe_raw(row)), stamp,
    )


def _payout_insert(db, row, payout_id, stamp):
    """Return explicit columns/values, including required legacy-only fields."""
    columns = list(PAYOUT_COLUMNS)
    values = list(_payout_values(row, payout_id, stamp))
    live = {item[1] for item in db.execute("PRAGMA table_info(sumup_payouts)")}
    if "transaction_code" in live:
        columns.append("transaction_code")
        values.append(row.get("transaction_code"))
    return tuple(columns), tuple(values)


class SumUpTransactionLedger:
    def __init__(self, path: Path | sqlite3.Connection):
        self.db = path if isinstance(path, sqlite3.Connection) else sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.schema_migration = migrate_sumup_schema(self.db, SCHEMA)

    def import_merchant(self, row):
        profile = row.get("merchant_profile", row)
        code = str(profile.get("merchant_code") or profile.get("merchant_id") or "")
        if not code: raise SumUpError("Profil marchand sans merchant_code", diagnostic={"category": "PARSING"})
        stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO sumup_merchants (merchant_code,legal_name,trading_name,country,currency,timezone,status,payout_settings_json,raw_json,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (code, profile.get("legal_name") or profile.get("company_name"), profile.get("doing_business_as") or profile.get("trading_name"), profile.get("country"), profile.get("currency"), profile.get("timezone"), profile.get("status"), json.dumps(_safe_raw(profile.get("payout_settings") or {})), json.dumps(_safe_raw(row)), stamp))

    def import_readers(self, page):
        inserted = 0; stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            for row in page.rows:
                rid = str(row.get("id") or row.get("reader_id") or "")
                if not rid: continue
                inserted += not bool(self.db.execute("SELECT 1 FROM sumup_readers WHERE reader_id=?", (rid,)).fetchone())
                self.db.execute("INSERT OR REPLACE INTO sumup_readers (reader_id,name,model,status,merchant_code,store_id,last_seen,software_version,raw_json,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, row.get("name"), row.get("model"), row.get("status"), row.get("merchant_code"), row.get("store_id"), row.get("last_seen") or row.get("last_seen_at"), row.get("software_version"), json.dumps(_safe_raw(row)), stamp))
        return {"rows_imported": inserted, "duplicates": len(page.rows) - inserted, "cursor": None}

    @staticmethod
    def _id(prefix, transaction_id, index, row):
        identifier = row.get("id") or row.get("event_id") or row.get("transaction_id")
        return str(identifier or f"{prefix}:{transaction_id}:{index}:{hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]}")

    def import_page(self, page):
        inserted = 0; stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            for row in page.rows:
                tid = str(row.get("id") or row.get("transaction_id") or row.get("transaction_code") or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
                inserted += not bool(self.db.execute("SELECT 1 FROM sumup_transactions WHERE sumup_transaction_id=?", (tid,)).fetchone())
                events = row.get("events") or []
                fee_value = row.get("fee_amount", row.get("fee", 0)); fee_value = fee_value.get("amount", 0) if isinstance(fee_value, dict) else fee_value
                card = row.get("card") or {}; terminal = row.get("terminal") or {}
                refunded = row.get("refunded_amount", sum(Decimal(str(e.get("amount", 0))) for e in events if str(e.get("type", "")).upper() == "REFUND"))
                chargeback = row.get("chargeback_amount", sum(Decimal(str(e.get("amount", 0))) for e in events if "CHARGEBACK" in str(e.get("type", "")).upper()))
                values = (tid, row.get("transaction_code"), _decimal(row.get("amount")), row.get("currency") or "EUR", str(row.get("timestamp") or row.get("date") or stamp), row.get("status"), row.get("simple_status") or row.get("status"), row.get("payment_type"), row.get("entry_mode"), row.get("card_type") or card.get("type") or card.get("scheme"), row.get("terminal_id") or terminal.get("id"), row.get("product_summary"), _decimal(row.get("vat_amount")), _decimal(row.get("tip_amount")), _decimal(refunded), _decimal(chargeback), row.get("foreign_transaction_id"), row.get("client_transaction_id"), row.get("reference") or row.get("description"), row.get("receipt_url"), _decimal(fee_value), json.dumps(_safe_raw(events)), json.dumps(_safe_raw(row)), stamp)
                self.db.execute("INSERT OR REPLACE INTO sumup_transactions (sumup_transaction_id,transaction_code,amount,currency,timestamp,status,simple_status,payment_type,entry_mode,card_type,terminal_id,product_summary,vat_amount,tip_amount,refunded_amount,chargeback_amount,foreign_transaction_id,client_transaction_id,reference,receipt_url,fee,events_json,raw_json,imported_at) VALUES(" + ",".join("?" * len(values)) + ")", values)
                fees = row.get("fees") or ([{"amount": fee_value, "type": "TRANSACTION", "currency": row.get("currency")}] if Decimal(_decimal(fee_value)) else [])
                for i, fee in enumerate(fees):
                    fid = self._id("fee", tid, i, fee); amount = fee.get("amount", fee.get("value", 0))
                    self.db.execute("INSERT OR REPLACE INTO sumup_fees (fee_id,sumup_transaction_id,fee_type,amount,currency,raw_json) VALUES(?,?,?,?,?,?)", (fid, tid, fee.get("type"), _decimal(amount), fee.get("currency") or row.get("currency"), json.dumps(_safe_raw(fee))))
                for i, event in enumerate(events):
                    eid = self._id("event", tid, i, event); etype = str(event.get("type") or event.get("event_type") or "UNKNOWN").upper()
                    self.db.execute("INSERT OR REPLACE INTO sumup_transaction_events (event_id,sumup_transaction_id,event_type,status,amount,currency,event_at,payout_id,raw_json) VALUES(?,?,?,?,?,?,?,?,?)", (eid, tid, etype, event.get("status"), _decimal(event.get("amount")), event.get("currency") or row.get("currency"), event.get("timestamp") or event.get("date"), event.get("payout_id"), json.dumps(_safe_raw(event))))
                    target = "sumup_refunds" if etype == "REFUND" else "sumup_chargebacks" if "CHARGEBACK" in etype else "sumup_reversals" if "REVERSAL" in etype else None
                    if target == "sumup_refunds":
                        self.db.execute("INSERT OR REPLACE INTO sumup_refunds (refund_id,sumup_transaction_id,amount,currency,refund_at,status,is_partial,reason,raw_json) VALUES(?,?,?,?,?,?,?,?,?)", (eid, tid, _decimal(event.get("amount")), event.get("currency") or row.get("currency"), event.get("timestamp") or event.get("date"), event.get("status"), int(Decimal(_decimal(event.get("amount"))) < Decimal(_decimal(row.get("amount")))), event.get("reason"), json.dumps(_safe_raw(event))))
                    elif target:
                        if target == "sumup_chargebacks":
                            self.db.execute("INSERT OR REPLACE INTO sumup_chargebacks (chargeback_id,sumup_transaction_id,amount,currency,chargeback_at,status,reason,raw_json) VALUES(?,?,?,?,?,?,?,?)", (eid, tid, _decimal(event.get("amount")), event.get("currency") or row.get("currency"), event.get("timestamp") or event.get("date"), event.get("status"), event.get("reason") or event.get("category"), json.dumps(_safe_raw(event))))
                        else:
                            self.db.execute("INSERT OR REPLACE INTO sumup_reversals (reversal_id,sumup_transaction_id,amount,currency,reversal_at,status,reason,payout_id,source,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, tid, _decimal(event.get("amount")), event.get("currency") or row.get("currency"), event.get("timestamp") or event.get("date"), event.get("status"), event.get("reason") or event.get("category"), event.get("payout_id"), "transaction_detail", json.dumps(_safe_raw(event))))
        return {"rows_imported": inserted, "duplicates": len(page.rows) - inserted, "cursor": page.next_cursor}

    def sync(self, provider, cursor=None, **bounds):
        total = duplicates = 0
        while True:
            page = provider.transactions(cursor, **bounds); result = self.import_page(page)
            total += result["rows_imported"]; duplicates += result["duplicates"]; cursor = page.next_cursor
            if not cursor or not _cursor(cursor).get("next"): break
        return {"rows_imported": total, "duplicates": duplicates, "cursor": cursor}

    def rows(self): return [dict(r) for r in self.db.execute("SELECT * FROM sumup_transactions ORDER BY timestamp DESC")]


class PaymentSettlementLedger:
    def __init__(self, path: Path | sqlite3.Connection):
        self.db = path if isinstance(path, sqlite3.Connection) else sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.schema_migration = migrate_sumup_schema(self.db, SCHEMA)

    def import_page(self, page):
        inserted = 0; stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            for row in page.rows:
                pid = str(row.get("payout_id") or row.get("id") or row.get("reference") or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
                inserted += not bool(self.db.execute("SELECT 1 FROM sumup_payouts WHERE payout_id=?", (pid,)).fetchone())
                items = row.get("items") or row.get("deductions") or row.get("transactions") or []
                insert_columns, values = _payout_insert(self.db, row, pid, stamp)
                columns = ",".join(insert_columns)
                self.db.execute("INSERT OR REPLACE INTO sumup_payouts (" + columns + ") VALUES(" + ",".join("?" * len(values)) + ")", values)
                if row.get("transaction_code") and not items: items = [row]
                for i, item in enumerate(items):
                    code = str(item.get("transaction_code") or item.get("transaction_id") or item.get("id") or "")
                    item_id = str(item.get("item_id") or f"{pid}:{code or i}:{item.get('type', 'ITEM')}")
                    tx = self.db.execute("SELECT sumup_transaction_id,amount,currency FROM sumup_transactions WHERE transaction_code=? OR sumup_transaction_id=?", (code, code)).fetchone() if code else None
                    self.db.execute("INSERT OR REPLACE INTO sumup_payout_items (item_id,payout_id,sumup_transaction_id,transaction_code,item_type,amount,currency,occurred_at,raw_json) VALUES(?,?,?,?,?,?,?,?,?)", (item_id, pid, tx["sumup_transaction_id"] if tx else None, code or None, item.get("type"), _decimal(item.get("amount")), item.get("currency") or row.get("currency"), item.get("timestamp") or item.get("date"), json.dumps(_safe_raw(item))))
                    if tx:
                        self.db.execute("INSERT OR IGNORE INTO payment_settlements (settlement_id,sumup_transaction_id,payout_id,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?)", (f"sumup:{tx['sumup_transaction_id']}:{pid}", tx["sumup_transaction_id"], pid, tx["amount"], tx["currency"], "MATCHED", stamp))
        return {"rows_imported": inserted, "duplicates": len(page.rows) - inserted, "cursor": page.next_cursor}

    def sync(self, provider, cursor=None, **bounds):
        total = duplicates = 0
        while True:
            page = provider.payouts(cursor, **bounds); result = self.import_page(page)
            total += result["rows_imported"]; duplicates += result["duplicates"]; cursor = page.next_cursor
            if cursor is None: break
        return {"rows_imported": total, "duplicates": duplicates, "cursor": cursor}

    def rows(self): return [dict(r) for r in self.db.execute("SELECT * FROM sumup_payouts ORDER BY payout_date DESC")]

    def cockpit(self):
        """Operational settlement view; amounts are evidence, never revenue."""
        one = lambda sql: dict(self.db.execute(sql).fetchone())
        transactions = one("""SELECT count(*) volume,coalesce(sum(CAST(amount AS NUMERIC)),0) amount,
            min(timestamp) period_start,max(timestamp) period_end,
            coalesce(sum(CAST(refunded_amount AS NUMERIC)),0) refunds,
            coalesce(sum(CAST(chargeback_amount AS NUMERIC)),0) chargebacks
            FROM sumup_transactions""")
        payouts = one("""SELECT count(*) volume,coalesce(sum(CAST(amount AS NUMERIC)),0) net,
            coalesce(sum(CAST(fee AS NUMERIC)),0) fees,min(start_date) period_start,max(end_date) period_end
            FROM sumup_payouts""")
        composition = one("""SELECT count(*) items,
            sum(CASE WHEN sumup_transaction_id IS NOT NULL THEN 1 ELSE 0 END) linked
            FROM sumup_payout_items""")
        states = {row["status"] or "UNKNOWN": row["n"] for row in self.db.execute(
            "SELECT status,count(*) n FROM payment_settlements GROUP BY status")}
        return {"transactions": transactions, "payouts": payouts,
                "composition": {**composition, "availability": "available" if composition["items"] else "unavailable"},
                "reconciliations": {key: states.get(key, 0) for key in ("MATCHED", "POSSIBLE", "UNMATCHED", "CONFLICT")},
                "revenue_included": False}

    def reconcile(self):
        created = 0; stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            for item in self.db.execute("SELECT payout_id,transaction_code FROM sumup_payout_items WHERE sumup_transaction_id IS NULL AND transaction_code IS NOT NULL").fetchall():
                tx = self.db.execute("SELECT sumup_transaction_id,amount,currency FROM sumup_transactions WHERE transaction_code=? OR sumup_transaction_id=?", (item["transaction_code"], item["transaction_code"])).fetchone()
                if tx:
                    self.db.execute("UPDATE sumup_payout_items SET sumup_transaction_id=? WHERE payout_id=? AND transaction_code=?", (tx["sumup_transaction_id"], item["payout_id"], item["transaction_code"]))
                    created += self.db.execute("INSERT OR IGNORE INTO payment_settlements (settlement_id,sumup_transaction_id,payout_id,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?)", (f"sumup:{tx['sumup_transaction_id']}:{item['payout_id']}", tx["sumup_transaction_id"], item["payout_id"], tx["amount"], tx["currency"], "MATCHED", stamp)).rowcount
        return self.db.execute("SELECT count(*) FROM payment_settlements").fetchone()[0]
