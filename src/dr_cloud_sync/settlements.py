"""Auditable, read-only links between ShopCaisse payments and SumUp evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
import sqlite3

from .sales_ingestion import OPERATIONAL_SCHEMA, canonicalize_payment_type
from .schema import ensure_schema

FINAL = {"SUCCESSFUL", "SUCCESS", "COMPLETED", "PAID", "VALIDATED"}
FAILED = {"FAILED", "CANCELLED", "CANCELED", "REVERSED", "REFUNDED"}
STATUSES = {"MATCHED", "POSSIBLE", "UNMATCHED", "CONFLICT", "REJECTED"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_settlement_links(
 settlement_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,source_id TEXT NOT NULL,
 target_type TEXT,target_id TEXT,status TEXT NOT NULL,confidence TEXT NOT NULL,
 match_method TEXT NOT NULL,amount_source TEXT,amount_target TEXT,amount_difference TEXT,
 currency TEXT,time_difference_seconds INTEGER,evidence_json TEXT NOT NULL,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,confirmed_at TEXT,confirmed_by TEXT,
 idempotency_key TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS idx_settlement_source ON payment_settlement_links(source_type,source_id,status);
CREATE INDEX IF NOT EXISTS idx_settlement_target ON payment_settlement_links(target_type,target_id,status);
CREATE INDEX IF NOT EXISTS idx_settlement_amount ON payment_settlement_links(amount_source,currency);
CREATE TABLE IF NOT EXISTS payment_settlement_evidence(
 evidence_id TEXT PRIMARY KEY,settlement_id TEXT NOT NULL,evidence_type TEXT NOT NULL,
 evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(settlement_id) REFERENCES payment_settlement_links(settlement_id));
CREATE TABLE IF NOT EXISTS payment_settlement_runs(
 run_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,completed_at TEXT,cursor TEXT,
 diagnostics_json TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_mapping_history(
 payment_id TEXT NOT NULL,old_category TEXT NOT NULL,new_category TEXT NOT NULL,
 old_rule TEXT NOT NULL,new_rule TEXT NOT NULL,old_version TEXT NOT NULL,new_version TEXT NOT NULL,
 changed_at TEXT NOT NULL,PRIMARY KEY(payment_id,new_version));
"""


@dataclass(frozen=True)
class PaymentSettlement:
    settlement_id: str
    status: str


@dataclass(frozen=True)
class PaymentSettlementLink:
    source_id: str
    target_id: str | None
    status: str
    match_method: str
    confidence: Decimal


@dataclass(frozen=True)
class PaymentSettlementEvidence:
    evidence_type: str
    values: dict


def _time(value: str, *, require_timezone=False) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if require_timezone and parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def match_payment(payment: dict, transactions: list[dict], *, priority_window_seconds=120, window_seconds=600) -> dict:
    """Pure deterministic matcher. A payment is evaluated independently of its ticket."""
    amount, currency = Decimal(str(payment["amount"])), payment.get("currency") or "EUR"
    try:
        occurred = _time(payment["occurred_at"], require_timezone=True)
    except (AttributeError, TypeError, ValueError):
        return {"status":"UNMATCHED","confidence":"0","match_method":"INVALID_PAYMENT_TIME","target":None,
                "evidence":{"quality_status":"INVALID","candidate_count":0}}
    reference = str(payment.get("external_payment_id") or "").strip()
    plausible = []
    exact = []
    for tx in transactions:
        if (tx.get("currency") or "EUR") != currency:
            continue
        seconds = int(abs((_time(tx["timestamp"]) - occurred).total_seconds()))
        refs = {str(tx.get(k) or "").strip() for k in
                ("sumup_transaction_id", "transaction_code", "client_transaction_id", "foreign_transaction_id", "reference")}
        candidate = {**tx, "time_difference_seconds": seconds}
        if reference and reference in refs:
            exact.append(candidate)
        if Decimal(str(tx["amount"])) == amount and seconds <= window_seconds:
            plausible.append(candidate)
    selected = exact if exact else plausible
    method = "EXACT_REFERENCE" if exact else "AMOUNT_CURRENCY_TIME_UNIQUE"
    if len(selected) > 1:
        return {"status": "CONFLICT", "confidence": "0", "match_method": "MULTIPLE_CANDIDATES", "target": None,
                "evidence": {"candidate_ids": sorted(x["sumup_transaction_id"] for x in selected), "candidate_count": len(selected)}}
    if not selected:
        return {"status": "UNMATCHED", "confidence": "0", "match_method": "NO_CANDIDATE", "target": None,
                "evidence": {"window_seconds": window_seconds, "candidate_count": 0}}
    target = selected[0]
    if str(target.get("status") or target.get("simple_status") or "").upper() in FAILED:
        return {"status": "CONFLICT", "confidence": "0", "match_method": method + "_NON_FINAL", "target": target,
                "evidence": {"transaction_status": target.get("status"), "candidate_count": 1}}
    confidence = "1" if exact else ("0.9" if target["time_difference_seconds"] <= priority_window_seconds else "0.82")
    return {"status": "MATCHED", "confidence": confidence, "match_method": method, "target": target,
            "evidence": {"reference_equal": bool(exact), "amount_equal": Decimal(str(target["amount"])) == amount,
                         "currency_equal": True, "candidate_count": 1}}


def match_payout(payout: dict, credits: list[dict], *, window_seconds=604800) -> dict:
    """Match a SumUp payout to one Qonto credit without amount-only guesses."""
    amount, currency = Decimal(str(payout["amount"])), payout.get("currency") or "EUR"
    occurred = _time(payout.get("paid_date") or payout["payout_date"])
    references = {str(payout.get(k) or "").strip().casefold() for k in ("payout_id", "reference")} - {""}
    plausible, exact = [], []
    for credit in credits:
        if credit.get("direction") != "CREDIT" or credit.get("currency") != currency: continue
        seconds = int(abs((_time(credit["booked_at"]) - occurred).total_seconds()))
        shared = any(ref in " ".join(str(credit.get(k) or "") for k in ("reference", "label")).casefold() for ref in references)
        counterparty_sumup = "sumup" in str(credit.get("counterparty") or "").casefold()
        candidate = {**credit, "time_difference_seconds": seconds}
        if shared: exact.append(candidate)
        if Decimal(str(credit["amount"])) == amount and seconds <= window_seconds and counterparty_sumup: plausible.append(candidate)
    selected = exact or plausible
    if len(selected) > 1:
        return {"status":"CONFLICT","confidence":"0","match_method":"MULTIPLE_BANK_CANDIDATES","target":None,
                "evidence":{"candidate_ids":sorted(x["transaction_id"] for x in selected),"candidate_count":len(selected)}}
    if not selected:
        return {"status":"UNMATCHED","confidence":"0","match_method":"NO_BANK_CANDIDATE","target":None,
                "evidence":{"candidate_count":0,"window_seconds":window_seconds}}
    target=selected[0]
    return {"status":"MATCHED","confidence":"1" if exact else "0.9","match_method":"EXACT_BANK_REFERENCE" if exact else "NET_CURRENCY_SUMUP_TIME_UNIQUE","target":target,
            "evidence":{"reference_equal":bool(exact),"amount_equal":Decimal(str(target["amount"]))==amount,"currency_equal":True,"counterparty_sumup":"sumup" in str(target.get("counterparty") or "").casefold(),"candidate_count":1}}


class PaymentSettlementService:
    """Projection-only settlement ledger; source ledgers are never mutated."""
    def __init__(self, db: sqlite3.Connection, *, priority_window_seconds=120, window_seconds=600, rounding_tolerance="0.01", qonto_configured=False, transit_window_days=14):
        self.db = db
        self.db.row_factory = sqlite3.Row
        ensure_schema(self.db, OPERATIONAL_SCHEMA, owner="ShopCaisse settlement source")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.window_seconds = int(window_seconds)
        self.priority_window_seconds = int(priority_window_seconds)
        self.tolerance = Decimal(str(rounding_tolerance))
        self.qonto_configured = bool(qonto_configured)
        self.transit_window_days = max(1, int(transit_window_days))
        self._backfill_payment_mapping()

    def _backfill_payment_mapping(self):
        """Upgrade already persisted payments; the raw provider label remains untouched."""
        current=canonicalize_payment_type(None)[2]
        rows=self.db.execute("SELECT payment_id,payment_type,name,description,canonical_payment_type,mapping_rule,mapping_version FROM sale_payments WHERE mapping_version!=? OR canonical_payment_type='UNKNOWN'",(current,)).fetchall()
        stamp=datetime.now(timezone.utc).isoformat()
        with self.db:
            for row in rows:
                category,rule,version=canonicalize_payment_type(row["payment_type"],row["name"],row["description"])
                quality="VALID" if category!="UNKNOWN" else "UNSUPPORTED"
                self.db.execute("INSERT OR IGNORE INTO payment_mapping_history VALUES(?,?,?,?,?,?,?,?)",(row["payment_id"],row["canonical_payment_type"],category,row["mapping_rule"],rule,row["mapping_version"],version,stamp))
                self.db.execute("UPDATE sale_payments SET canonical_payment_type=?,mapping_rule=?,mapping_version=?,quality_status=?,quality_reason=? WHERE payment_id=?",
                    (category,rule,version,quality,None if quality=="VALID" else "unknown payment type",row["payment_id"]))

    def shopcaisse_audit(self):
        """Return a deliberately aggregate-only payment audit (no source IDs)."""
        grouped={}
        for field in ("payment_type","name","description"):
            grouped[field]=[{"value":r[0] if r[0] not in (None,"") else None,"count":r[1],"amount":str(r[2] or 0)}
                for r in self.db.execute(f"SELECT {field},count(*),sum(CAST(amount AS NUMERIC)) FROM sale_payments GROUP BY {field} ORDER BY count(*) DESC")]
        period=self.db.execute("SELECT min(coalesce(p.occurred_at,s.sold_at)),max(coalesce(p.occurred_at,s.sold_at)) FROM sale_payments p JOIN sales s USING(sale_id) WHERE s.source='SHOPCAISSE'").fetchone()
        categories={r[0]:{"count":r[1],"amount":str(r[2] or 0)} for r in self.db.execute("SELECT canonical_payment_type,count(*),sum(CAST(amount AS NUMERIC)) FROM sale_payments GROUP BY canonical_payment_type")}
        state=self.db.execute("SELECT last_report_json FROM sales_sync_states WHERE source='SHOPCAISSE'").fetchone()
        try: api_report=json.loads(state[0] or "{}") if state else {}
        except json.JSONDecodeError: api_report={}
        evidence_keys=("shopcaisse_payments","sales_endpoint","tickets_observed","tickets_with_payments_key","payment_objects_observed","official_alternative")
        api_evidence={key:api_report[key] for key in evidence_keys if key in api_report}
        return {"total":self.db.execute("SELECT count(*) FROM sale_payments").fetchone()[0],"raw_values":grouped,
            "categories":categories,"without_type":self.db.execute("SELECT count(*) FROM sale_payments WHERE trim(coalesce(payment_type,''))='' ").fetchone()[0],
            "zero_amount":self.db.execute("SELECT count(*) FROM sale_payments WHERE CAST(amount AS NUMERIC)=0").fetchone()[0],
            "period":{"start":period[0],"end":period[1]},"mixed_tickets":self.db.execute("SELECT count(*) FROM (SELECT sale_id FROM sale_payments GROUP BY sale_id HAVING count(*)>1)").fetchone()[0],
            "mapping_version":canonicalize_payment_type(None)[2],"api_evidence":api_evidence,
            "exclusions":self.payment_exclusions()}

    def payment_exclusions(self):
        """Return aggregate, mutually-exclusive settlement rejection reasons."""
        rows=self.db.execute("""SELECT p.*,s.sale_id parent_id,s.source sale_source,s.status sale_status
          FROM sale_payments p LEFT JOIN sales s ON s.sale_id=p.sale_id""").fetchall()
        counts={key:0 for key in ("NON_CARD","INVALID_AMOUNT","MISSING_DATE","MISSING_CURRENCY","CANCELLED","ORPHAN","DUPLICATE","OTHER")}
        seen=set()
        for row in rows:
            identity=(row["sale_id"],row["external_payment_id"])
            if row["parent_id"] is None: reason="ORPHAN"
            elif identity in seen: reason="DUPLICATE"
            elif row["canonical_payment_type"]!="CARD": reason="NON_CARD"
            elif Decimal(str(row["amount"] or 0))<=0: reason="INVALID_AMOUNT"
            elif not row["occurred_at"]: reason="MISSING_DATE"
            elif not row["currency"]: reason="MISSING_CURRENCY"
            elif str(row["status"] or "").upper() in {"CANCELLED","CANCELED","REFUNDED","REVERSED"} or str(row["sale_status"] or "").upper() in {"CANCELLED","CANCELED","REFUNDED"}: reason="CANCELLED"
            elif row["sale_source"]!="SHOPCAISSE" or row["quality_status"]!="VALID": reason="OTHER"
            else: seen.add(identity); continue
            counts[reason]+=1; seen.add(identity)
        return counts

    @staticmethod
    def _key(source_type, source_id, target_type, target_id):
        return hashlib.sha256(f"{source_type}:{source_id}:{target_type}:{target_id or '-'}".encode()).hexdigest()

    def recompute(self, *, limit=500, after=None):
        args = [after] if after else []
        clause = "AND p.payment_id>?" if after else ""
        payments = [dict(r) for r in self.db.execute(f"""SELECT coalesce(p.occurred_at,s.sold_at) occurred_at,
          coalesce(p.currency,s.currency) currency,p.*,s.location,s.external_sale_id
          FROM sale_payments p JOIN sales s ON s.sale_id=p.sale_id
          WHERE s.source='SHOPCAISSE' AND p.canonical_payment_type='CARD' AND p.quality_status='VALID' {clause}
          ORDER BY p.payment_id LIMIT ?""", (*args, int(limit)))]
        transactions = [dict(r) for r in self.db.execute("SELECT * FROM sumup_transactions")]
        by_amount={}; by_reference={}
        for tx in transactions:
            by_amount.setdefault((str(tx.get("currency") or "EUR"),Decimal(str(tx["amount"]))),[]).append(tx)
            for field in ("sumup_transaction_id","transaction_code","client_transaction_id","foreign_transaction_id","reference"):
                ref=str(tx.get(field) or "").strip()
                if ref: by_reference.setdefault((str(tx.get("currency") or "EUR"),ref),[]).append(tx)
        stamp = datetime.now(timezone.utc).isoformat()
        counts = {x.lower(): 0 for x in ("MATCHED", "POSSIBLE", "UNMATCHED", "CONFLICT")}
        with self.db:
            for payment in payments:
                candidates=list(by_amount.get((payment["currency"],Decimal(str(payment["amount"]))),()))
                for tx in by_reference.get((payment["currency"],str(payment["external_payment_id"]).strip()),()):
                    if tx not in candidates: candidates.append(tx)
                proposal = match_payment(payment, candidates, priority_window_seconds=self.priority_window_seconds, window_seconds=self.window_seconds)
                target = proposal["target"]
                target_id = target["sumup_transaction_id"] if target else None
                key = self._key("SHOPCAISSE_PAYMENT", payment["payment_id"], "SUMUP_TRANSACTION", target_id)
                sid = "settlement:" + key[:24]
                difference = str(Decimal(str(target["amount"])) - Decimal(str(payment["amount"]))) if target else None
                evidence = json.dumps(proposal["evidence"], sort_keys=True)
                # Recompute cannot overwrite a human decision.
                existing = self.db.execute("SELECT confirmed_by,status FROM payment_settlement_links WHERE idempotency_key=?", (key,)).fetchone()
                if existing and existing["confirmed_by"]:
                    counts[existing["status"].lower()] = counts.get(existing["status"].lower(), 0) + 1
                    continue
                self.db.execute("""INSERT INTO payment_settlement_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(idempotency_key) DO UPDATE SET status=excluded.status,confidence=excluded.confidence,
                  match_method=excluded.match_method,amount_target=excluded.amount_target,amount_difference=excluded.amount_difference,
                  time_difference_seconds=excluded.time_difference_seconds,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""",
                  (sid,"SHOPCAISSE_PAYMENT",payment["payment_id"],"SUMUP_TRANSACTION",target_id,proposal["status"],proposal["confidence"],proposal["match_method"],str(payment["amount"]),str(target["amount"]) if target else None,difference,payment["currency"],target.get("time_difference_seconds") if target else None,evidence,stamp,stamp,None,None,key))
                self.db.execute("INSERT OR IGNORE INTO payment_settlement_evidence VALUES(?,?,?,?,?)",
                                ("evidence:"+key[:24],sid,"MATCH_SIGNALS",evidence,stamp))
                counts[proposal["status"].lower()] += 1
            payouts = [dict(r) for r in self.db.execute("SELECT * FROM sumup_payouts ORDER BY payout_id")]
            has_bank=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_transactions'").fetchone()
            credits = [dict(r) for r in self.db.execute("SELECT * FROM bank_transactions WHERE provider='Qonto' AND direction='CREDIT'")] if has_bank else []
            for payout in payouts:
                # A missing bank source is not a failed match.  Keep one neutral
                # projection per payout so it becomes evaluable when Qonto is enabled.
                proposal=(match_payout(payout,credits) if self.qonto_configured else
                    {"status":"NOT_EVALUATED","confidence":"0","match_method":"WAITING_FOR_BANK_SOURCE",
                     "target":None,"evidence":{"candidate_count":None,"reason":"QONTO_NOT_CONFIGURED"}})
                target=proposal["target"]
                target_id=target["transaction_id"] if target else None
                key=self._key("SUMUP_PAYOUT",payout["payout_id"],"QONTO_CREDIT",target_id); sid="settlement:"+key[:24]
                existing=self.db.execute("SELECT confirmed_by FROM payment_settlement_links WHERE idempotency_key=?",(key,)).fetchone()
                if existing and existing["confirmed_by"]: continue
                difference=str(Decimal(str(target["amount"]))-Decimal(str(payout["amount"]))) if target else None
                evidence=json.dumps(proposal["evidence"],sort_keys=True)
                self.db.execute("""INSERT INTO payment_settlement_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(idempotency_key) DO UPDATE SET status=excluded.status,confidence=excluded.confidence,
                  match_method=excluded.match_method,amount_target=excluded.amount_target,amount_difference=excluded.amount_difference,
                  time_difference_seconds=excluded.time_difference_seconds,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""",
                  (sid,"SUMUP_PAYOUT",payout["payout_id"],"QONTO_CREDIT",target_id,proposal["status"],proposal["confidence"],proposal["match_method"],str(payout["amount"]),str(target["amount"]) if target else None,difference,payout["currency"],target.get("time_difference_seconds") if target else None,evidence,stamp,stamp,None,None,key))
                self.db.execute("INSERT OR IGNORE INTO payment_settlement_evidence VALUES(?,?,?,?,?)",("evidence:"+key[:24],sid,"BANK_MATCH_SIGNALS",evidence,stamp))
        return {"analysed": len(payments), "last_cursor": payments[-1]["payment_id"] if payments else after, **counts}

    def backfill(self, *, batch_size=500):
        run_id = "run:" + hashlib.sha256(datetime.now(timezone.utc).isoformat().encode()).hexdigest()[:20]
        stamp = datetime.now(timezone.utc).isoformat(); cursor = None
        total = {"analysed": 0, "matched": 0, "possible": 0, "unmatched": 0, "conflict": 0}
        with self.db: self.db.execute("INSERT INTO payment_settlement_runs VALUES(?,?,NULL,NULL,'{}','RUNNING')", (run_id, stamp))
        while True:
            result = self.recompute(limit=batch_size, after=cursor)
            for key in total: total[key] += result[key]
            cursor = result["last_cursor"]
            if result["analysed"] < batch_size: break
        period = self.db.execute("SELECT min(sold_at),max(sold_at) FROM sales WHERE source='SHOPCAISSE'").fetchone()
        ranges=self._source_ranges()
        diagnostics = {**total, "period_start": ranges["intersection_start"], "period_end": ranges["intersection_end"],
                       "shopcaisse_payments": total["analysed"],
                       "sumup_transactions": self.db.execute("SELECT count(*) FROM sumup_transactions").fetchone()[0],
                       "payouts": self.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0]}
        with self.db: self.db.execute("UPDATE payment_settlement_runs SET completed_at=?,cursor=?,diagnostics_json=?,status='COMPLETED' WHERE run_id=?", (datetime.now(timezone.utc).isoformat(), cursor, json.dumps(diagnostics), run_id))
        return {"run_id": run_id, **diagnostics}

    def _source_ranges(self):
        shop=self.db.execute("SELECT min(coalesce(p.occurred_at,s.sold_at)),max(coalesce(p.occurred_at,s.sold_at)) FROM sale_payments p JOIN sales s USING(sale_id) WHERE s.source='SHOPCAISSE' AND p.canonical_payment_type='CARD' AND p.quality_status='VALID'").fetchone()
        sumup=self.db.execute("SELECT min(timestamp),max(timestamp) FROM sumup_transactions WHERE upper(coalesce(status,simple_status,'')) IN ('SUCCESSFUL','SUCCESS','COMPLETED','PAID','VALIDATED')").fetchone()
        start=max(x for x in (shop[0],sumup[0]) if x is not None) if shop[0] and sumup[0] else None
        end=min(x for x in (shop[1],sumup[1]) if x is not None) if shop[1] and sumup[1] else None
        if start and end and start>end: start=end=None
        return {"shopcaisse_start":shop[0],"shopcaisse_end":shop[1],"sumup_start":sumup[0],"sumup_end":sumup[1],"intersection_start":start,"intersection_end":end}

    def backfill_preview(self):
        ranges=self._source_ranges()
        return {**ranges,"card_candidates":self.db.execute("SELECT count(*) FROM sale_payments WHERE canonical_payment_type='CARD' AND quality_status='VALID'").fetchone()[0],
                "final_sumup_transactions":self.db.execute("SELECT count(*) FROM sumup_transactions WHERE upper(coalesce(status,simple_status,'')) IN ('SUCCESSFUL','SUCCESS','COMPLETED','PAID','VALIDATED')").fetchone()[0]}

    def review(self, settlement_id, status, actor):
        if status not in {"MATCHED", "POSSIBLE", "REJECTED", "UNMATCHED"}: raise ValueError("Décision invalide")
        stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            changed = self.db.execute("UPDATE payment_settlement_links SET status=?,confirmed_at=?,confirmed_by=?,updated_at=? WHERE settlement_id=?", (status, stamp, actor, stamp, settlement_id)).rowcount
        if not changed: raise KeyError(settlement_id)
        return dict(self.db.execute("SELECT * FROM payment_settlement_links WHERE settlement_id=?", (settlement_id,)).fetchone())

    def note(self, settlement_id, note, actor):
        note=str(note or "").strip()
        if not note or len(note)>1000: raise ValueError("Note invalide")
        if not self.db.execute("SELECT 1 FROM payment_settlement_links WHERE settlement_id=?",(settlement_id,)).fetchone(): raise KeyError(settlement_id)
        stamp=datetime.now(timezone.utc).isoformat(); key=hashlib.sha256(f"{settlement_id}:{actor}:{note}".encode()).hexdigest()
        with self.db: self.db.execute("INSERT OR IGNORE INTO payment_settlement_evidence VALUES(?,?,?,?,?)",("note:"+key[:24],settlement_id,"INTERNAL_NOTE",json.dumps({"note":note,"actor":actor}),stamp))
        return {"settlement_id":settlement_id,"saved":True}

    def details(self, settlement_id):
        link=self.db.execute("SELECT * FROM payment_settlement_links WHERE settlement_id=?",(settlement_id,)).fetchone()
        if not link: raise KeyError(settlement_id)
        payment=None; transaction=None
        if link["source_type"]=="SHOPCAISSE_PAYMENT":
            row=self.db.execute("""SELECT p.external_payment_id,s.external_sale_id,p.payment_type,p.canonical_payment_type,
              p.mapping_rule,p.mapping_version,p.amount,p.currency,p.occurred_at,p.status,p.name,p.description,p.source,p.store_id,p.imported_at,p.quality_status
              FROM sale_payments p JOIN sales s USING(sale_id) WHERE p.payment_id=?""",(link["source_id"],)).fetchone()
            payment=dict(row) if row else None
        if link["target_type"]=="SUMUP_TRANSACTION" and link["target_id"]:
            row=self.db.execute("SELECT sumup_transaction_id,transaction_code,amount,currency,timestamp,status,simple_status FROM sumup_transactions WHERE sumup_transaction_id=?",(link["target_id"],)).fetchone()
            transaction=dict(row) if row else None
        return {"link":dict(link),"payment":payment,"transaction":transaction,"evidence":[dict(r) for r in self.db.execute("SELECT evidence_type,evidence_json,created_at FROM payment_settlement_evidence WHERE settlement_id=? ORDER BY created_at",(settlement_id,))]}

    @staticmethod
    def _safe_json(value):
        """Decode evidence while keeping credentials and raw connector payloads out."""
        try: data=json.loads(value or "{}")
        except (TypeError,json.JSONDecodeError): return {}
        forbidden=re.compile(r"(?i)(pan|cvv|token|authorization|secret|password|payload|raw)")
        return {str(k):v for k,v in data.items() if not forbidden.search(str(k))}

    def explorer(self, params=None):
        """Server-side, bounded explorer query over the settlement projection."""
        p=params or {}; page=max(1,int(p.get("page",1))); limit=min(100,max(1,int(p.get("limit",25))))
        where=[]; args=[]
        q=" ".join(str(p.get("q") or "").split()).casefold()
        if q:
            like=f"%{q}%"; where.append("(lower(replace(l.source_id,' ','')) LIKE replace(?,' ','') OR lower(replace(coalesce(l.target_id,''),' ','')) LIKE replace(?,' ','') OR lower(coalesce(sp.external_payment_id,'')) LIKE ? OR lower(coalesce(ss.external_sale_id,'')) LIKE ? OR lower(coalesce(st.transaction_code,'')) LIKE ? OR lower(coalesce(l.match_method,'')) LIKE ? OR lower(coalesce(e.evidence_json,'')) LIKE ? OR lower(coalesce(n.evidence_json,'')) LIKE ? OR CAST(l.amount_source AS TEXT) LIKE ? OR substr(coalesce(sp.occurred_at,l.updated_at),1,10)=?)")
            args.extend([like,like,like,like,like,like,like,like,like,q])
        allowed={"status":"l.status","currency":"l.currency","source_type":"l.source_type","target_type":"l.target_type"}
        for key,column in allowed.items():
            if p.get(key): where.append(f"{column}=?"); args.append(str(p[key]).upper() if key in {"status","source_type","target_type"} else p[key])
        if p.get("exclude_resolved"): where.append("l.status NOT IN ('MATCHED','REJECTED')")
        for key,op in (("amount_min",">="),("amount_max","<=")):
            if p.get(key) not in (None,""): where.append(f"CAST(l.amount_source AS NUMERIC){op}?");args.append(str(p[key]))
        if p.get("from"): where.append("date(l.updated_at)>=date(?)");args.append(p["from"])
        if p.get("to"): where.append("date(l.updated_at)<=date(?)");args.append(p["to"])
        for key,column in (("has_ticket","l.source_type='SHOPCAISSE_PAYMENT'"),("has_qonto","l.target_type='QONTO_CREDIT'")):
            if str(p.get(key,"" )).lower() in {"true","false"}: where.append(column if str(p[key]).lower()=="true" else f"NOT ({column})")
        clause=" WHERE "+" AND ".join(where) if where else ""
        base=""" FROM payment_settlement_links l
          LEFT JOIN sale_payments sp ON l.source_type='SHOPCAISSE_PAYMENT' AND sp.payment_id=l.source_id
          LEFT JOIN sales ss ON ss.sale_id=sp.sale_id
          LEFT JOIN sumup_transactions st ON l.target_type='SUMUP_TRANSACTION' AND st.sumup_transaction_id=l.target_id
          LEFT JOIN payment_settlement_evidence e ON e.evidence_id=(SELECT evidence_id FROM payment_settlement_evidence WHERE settlement_id=l.settlement_id AND evidence_type!='INTERNAL_NOTE' ORDER BY created_at DESC LIMIT 1)
          LEFT JOIN payment_settlement_evidence n ON n.evidence_id=(SELECT evidence_id FROM payment_settlement_evidence WHERE settlement_id=l.settlement_id AND evidence_type='INTERNAL_NOTE' ORDER BY created_at DESC LIMIT 1)"""
        total=self.db.execute("SELECT count(*)"+base+clause,args).fetchone()[0]
        sorts={"date":"l.updated_at","amount":"CAST(l.amount_source AS NUMERIC)","confidence":"CAST(l.confidence AS NUMERIC)","status":"l.status","difference":"CAST(l.amount_difference AS NUMERIC)"}
        order=sorts.get(p.get("sort"),"l.updated_at"); direction="ASC" if str(p.get("direction")).lower()=="asc" else "DESC"
        rows=[]
        for row in self.db.execute("SELECT l.*"+base+clause+f" ORDER BY {order} {direction},l.settlement_id LIMIT ? OFFSET ?",(*args,limit,(page-1)*limit)):
            item=dict(row); item["result_type"]="payout" if item["source_type"]=="SUMUP_PAYOUT" else "payment"; rows.append(item)
        return {"items":rows,"pagination":{"page":page,"limit":limit,"total":total,"pages":max(1,(total+limit-1)//limit)}}

    def evidence(self, settlement_id):
        detail=self.details(settlement_id); link=detail["link"]
        return {"settlement_id":settlement_id,"method":link["match_method"],"status":link["status"],"confidence":link["confidence"],"amount_source":link["amount_source"],"amount_target":link["amount_target"],"difference":link["amount_difference"],"time_difference_seconds":link["time_difference_seconds"],"currency":link["currency"],"signals":[{"type":x["evidence_type"],"values":self._safe_json(x["evidence_json"])} for x in detail["evidence"] if x["evidence_type"]!="INTERNAL_NOTE"],"decision":"HUMAN" if link["confirmed_by"] else "AUTOMATIC"}

    def timeline(self, settlement_id):
        detail=self.details(settlement_id); link=detail["link"]
        events=[{"date":link["created_at"],"source":"Settlement","type":"PROPOSITION","actor":"Moteur","result":link["status"]}]
        for x in detail["evidence"]:
            values=self._safe_json(x["evidence_json"]); events.append({"date":x["created_at"],"source":"Utilisateur" if x["evidence_type"]=="INTERNAL_NOTE" else "Moteur","type":x["evidence_type"],"actor":values.get("actor","Moteur"),"result":values.get("note","Preuve enregistrée")})
        if link["confirmed_at"]: events.append({"date":link["confirmed_at"],"source":"Settlement","type":"DECISION_HUMAINE","actor":link["confirmed_by"],"result":link["status"]})
        return {"items":sorted(events,key=lambda x:x["date"])}

    def anomalies(self, params=None):
        p=dict(params or {}); p.setdefault("limit",25); p["exclude_resolved"]=True; result=self.explorer(p)
        result["items"]=[{**x,"priority":"HIGH" if x["status"]=="CONFLICT" else "NORMAL","impact":x.get("amount_source"),"recommended_action":"Résoudre le conflit" if x["status"]=="CONFLICT" else "Examiner les candidats"} for x in result["items"]]
        return result

    def matches(self, status=None):
        sql = "SELECT * FROM payment_settlement_links"; args = ()
        if status: sql += " WHERE status=?"; args = (status,)
        return [dict(r) for r in self.db.execute(sql+" ORDER BY updated_at DESC", args)]

    def payout(self, payout_id):
        payout = self.db.execute("SELECT * FROM sumup_payouts WHERE payout_id=?", (payout_id,)).fetchone()
        if not payout: raise KeyError(payout_id)
        items = [dict(r) for r in self.db.execute("SELECT * FROM sumup_payout_items WHERE payout_id=?", (payout_id,))]
        if not items:
            return {"payout": dict(payout), "items": [], "composition": "UNAVAILABLE", "balance_status": "UNAVAILABLE"}
        gross = sum((Decimal(str(x["amount"] or 0)) for x in items if str(x["item_type"] or "").upper() in {"TRANSACTION","PAYMENT","SALE"}), Decimal())
        refunds = sum((abs(Decimal(str(x["amount"] or 0))) for x in items if "REFUND" in str(x["item_type"] or "").upper()), Decimal())
        chargebacks = sum((abs(Decimal(str(x["amount"] or 0))) for x in items if "CHARGEBACK" in str(x["item_type"] or "").upper()), Decimal())
        adjustments = sum((Decimal(str(x["amount"] or 0)) for x in items if "ADJUST" in str(x["item_type"] or "").upper()), Decimal())
        fees = Decimal(str(payout["fee"] or 0)); expected = gross-fees-refunds-chargebacks+adjustments
        difference = expected-Decimal(str(payout["amount"])); balanced = abs(difference) <= self.tolerance
        return {"payout": dict(payout), "items": items, "composition": "AVAILABLE", "balance_status": "BALANCED" if balanced else "UNBALANCED", "gross": str(gross), "fees": str(fees), "refunds": str(refunds), "chargebacks": str(chargebacks), "adjustments": str(adjustments), "expected_net": str(expected), "difference": str(difference), "tolerance": str(self.tolerance)}

    def summary(self):
        counts = {x: 0 for x in STATUSES}
        counts.update({r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM payment_settlement_links WHERE source_type='SHOPCAISSE_PAYMENT' GROUP BY status")})
        card_count, card_amount = self.db.execute("""SELECT count(*),coalesce(sum(CAST(p.amount AS NUMERIC)),0) FROM sale_payments p JOIN sales s USING(sale_id) WHERE s.source='SHOPCAISSE' AND p.canonical_payment_type='CARD' AND p.quality_status='VALID'""").fetchone()
        matched_amount = self.db.execute("SELECT coalesce(sum(CAST(amount_source AS NUMERIC)),0) FROM payment_settlement_links WHERE status='MATCHED' AND source_type='SHOPCAISSE_PAYMENT'").fetchone()[0]
        payout_states = {"BALANCED": 0, "PARTIAL": 0, "UNBALANCED": 0, "UNAVAILABLE": 0}
        for row in self.db.execute("SELECT payout_id FROM sumup_payouts"):
            payout_states[self.payout(row[0])["balance_status"]] += 1
        has_bank=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_transactions'").fetchone()
        qonto_available=self.qonto_configured or bool(has_bank and self.db.execute("SELECT 1 FROM bank_accounts WHERE provider='Qonto' LIMIT 1").fetchone())
        qonto_credits=self.db.execute("SELECT count(*) FROM bank_transactions WHERE provider='Qonto' AND direction='CREDIT'").fetchone()[0] if qonto_available else None
        bank_union=" UNION ALL SELECT booked_at FROM bank_transactions WHERE provider='Qonto'" if has_bank else ""
        period=self.db.execute("SELECT min(d),max(d) FROM (SELECT sold_at d FROM sales WHERE source='SHOPCAISSE' UNION ALL SELECT timestamp FROM sumup_transactions UNION ALL SELECT payout_date FROM sumup_payouts"+bank_union+")").fetchone()
        matched_count=counts["MATCHED"]
        coverage_count = Decimal(matched_count) / Decimal(card_count) * 100 if card_count else None
        coverage_amount = Decimal(str(matched_amount)) / Decimal(str(card_amount)) * 100 if card_amount else None
        quality={r[0]:r[1] for r in self.db.execute("SELECT quality_status,count(*) FROM sale_payments GROUP BY quality_status")}
        payout_linked=self.db.execute("SELECT count(DISTINCT sumup_transaction_id) FROM payment_settlements").fetchone()[0]
        final_where="upper(coalesce(status,simple_status,'')) IN ('SUCCESSFUL','SUCCESS','COMPLETED','PAID','VALIDATED')"
        tx=self.db.execute(f"SELECT count(*),coalesce(sum(CAST(amount AS NUMERIC)),0),coalesce(sum(CAST(fee AS NUMERIC)),0),coalesce(sum(CAST(refunded_amount AS NUMERIC)),0),coalesce(sum(CAST(chargeback_amount AS NUMERIC)),0) FROM sumup_transactions WHERE {final_where}").fetchone()
        valid="s.source='SHOPCAISSE' AND p.quality_status='VALID' AND upper(coalesce(p.status,'')) NOT IN ('CANCELLED','CANCELED','REFUNDED','REVERSED') AND upper(coalesce(s.status,'')) NOT IN ('CANCELLED','CANCELED','REFUNDED')"
        cash=self.db.execute(f"SELECT count(*),sum(CAST(p.amount AS NUMERIC)) FROM sale_payments p JOIN sales s USING(sale_id) WHERE {valid} AND p.canonical_payment_type='CASH' AND date(coalesce(p.occurred_at,s.sold_at))=date('now')").fetchone()
        card_today=self.db.execute(f"SELECT count(*),sum(CAST(p.amount AS NUMERIC)) FROM sale_payments p JOIN sales s USING(sale_id) WHERE {valid} AND p.canonical_payment_type='CARD' AND date(coalesce(p.occurred_at,s.sold_at))=date('now')").fetchone()
        today=self.db.execute(f"SELECT count(*),sum(CAST(p.amount AS NUMERIC)) FROM sale_payments p JOIN sales s USING(sale_id) WHERE {valid} AND p.canonical_payment_type!='UNKNOWN' AND date(coalesce(p.occurred_at,s.sold_at))=date('now')").fetchone()
        unknown_today=self.db.execute(f"SELECT count(*) FROM sale_payments p JOIN sales s USING(sale_id) WHERE s.source='SHOPCAISSE' AND date(coalesce(p.occurred_at,s.sold_at))=date('now') AND (p.quality_status!='VALID' OR p.canonical_payment_type='UNKNOWN')").fetchone()[0]
        paid=self.db.execute("SELECT coalesce(sum(CAST(amount_source AS NUMERIC)),0) FROM payment_settlement_links WHERE source_type='SUMUP_PAYOUT' AND status='MATCHED' AND target_id IS NOT NULL").fetchone()[0]
        pending_states=("PENDING","SCHEDULED","IN_PROGRESS","PROCESSING")
        pending_marks=",".join("?" for _ in pending_states)
        pending_rows=self.db.execute(f"SELECT payout_id,amount,coalesce(paid_date,payout_date),status FROM sumup_payouts WHERE upper(coalesce(status,'')) IN ({pending_marks}) ORDER BY payout_date LIMIT 20",pending_states).fetchall()
        pending_payout=sum((Decimal(str(r[1])) for r in pending_rows),Decimal())
        cutoff=(datetime.now(timezone.utc)-timedelta(days=self.transit_window_days)).isoformat()
        unassigned=self.db.execute("SELECT coalesce(sum(CAST(t.amount AS NUMERIC)-CAST(t.fee AS NUMERIC)-CAST(t.refunded_amount AS NUMERIC)-CAST(t.chargeback_amount AS NUMERIC)),0) FROM sumup_transactions t LEFT JOIN payment_settlements p ON p.sumup_transaction_id=t.sumup_transaction_id WHERE p.settlement_id IS NULL AND upper(coalesce(t.status,t.simple_status,'')) IN ('SUCCESSFUL','SUCCESS','COMPLETED','PAID','VALIDATED') AND t.timestamp>=?",(cutoff,)).fetchone()[0]
        historical_unassigned=self.db.execute("SELECT coalesce(sum(CAST(t.amount AS NUMERIC)-CAST(t.fee AS NUMERIC)-CAST(t.refunded_amount AS NUMERIC)-CAST(t.chargeback_amount AS NUMERIC)),0) FROM sumup_transactions t LEFT JOIN payment_settlements p ON p.sumup_transaction_id=t.sumup_transaction_id WHERE p.settlement_id IS NULL AND upper(coalesce(t.status,t.simple_status,'')) IN ('SUCCESSFUL','SUCCESS','COMPLETED','PAID','VALIDATED') AND t.timestamp<?",(cutoff,)).fetchone()[0]
        fees_available=bool(self.db.execute("SELECT 1 FROM sumup_fees LIMIT 1").fetchone() or self.db.execute("SELECT 1 FROM sumup_transactions WHERE CAST(fee AS NUMERIC)!=0 LIMIT 1").fetchone() or self.db.execute("SELECT 1 FROM sumup_transactions WHERE json_type(raw_json,'$.fee') IS NOT NULL OR json_type(raw_json,'$.fee_amount') IS NOT NULL LIMIT 1").fetchone())
        # Without a bank authority, emitted payouts are not asserted as money due.
        transit=Decimal(str(unassigned))+(Decimal(str(pending_payout)) if qonto_available else Decimal())
        active_clause="status IN ('UNMATCHED','POSSIBLE','CONFLICT') AND NOT (source_type='SUMUP_PAYOUT' AND match_method='WAITING_FOR_BANK_SOURCE')"
        active_count=self.db.execute(f"SELECT count(*) FROM payment_settlement_links WHERE {active_clause}").fetchone()[0]
        configuration=({"code":"QONTO_NOT_CONFIGURED","status":"NOT_CONFIGURED","count":self.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0],"message":"Qonto n’est pas configuré : les payouts ne peuvent pas encore être vérifiés.","action":"Configurer Qonto"} if not qonto_available else None)
        return {"shopcaisse_card_payments":card_count,"shopcaisse_card_amount":str(card_amount),"sumup_transactions":tx[0],"payouts":self.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0],"payout_linked_transactions":payout_linked,"qonto_credits":qonto_credits,"counts":counts,"matched_amount":str(matched_amount),"unmatched_amount":str(max(Decimal(),Decimal(str(card_amount))-Decimal(str(matched_amount)))),"coverage_percent":str(coverage_amount) if coverage_amount is not None else None,"coverage_count_percent":str(coverage_count) if coverage_count is not None else None,"coverage_amount_percent":str(coverage_amount) if coverage_amount is not None else None,"coverage_metric":"AMOUNT","payment_quality":quality,"windows_seconds":{"priority":self.priority_window_seconds,"extension":self.window_seconds},"payout_balance":payout_states,"period":{"start":period[0],"end":period[1]},"common_period":self._source_ranges(),"revenue_included":False,
          "cash_summary":{"total_collected_today":str(today[1]) if today[0] and not unknown_today else None,"total_collected_reliable":not bool(unknown_today),"card_declared":str(card_today[1]) if card_today[0] else None,"card_count_today":card_today[0],"card_processed":str(tx[1]) if tx[0] else None,"cash":str(cash[1]) if cash[0] else None,"cash_count_today":cash[0],"period":"TODAY_UTC","fees":str(tx[2]) if fees_available else None,"refunds":str(tx[3]) if tx[0] else None,"chargebacks":str(tx[4]) if tx[0] else None,"paid":str(paid) if qonto_available else None,"unmatched":str(max(Decimal(),Decimal(str(card_amount))-Decimal(str(matched_amount))))},
          "in_transit":{"amount":str(transit),"transactions_without_payout":str(unassigned),"confirmed":str(unassigned),"payout_association_unavailable":str(pending_payout),"historical_unreconciled":str(historical_unassigned),"window_days":self.transit_window_days,"payouts_waiting_bank":str(pending_payout),"partially_matched":None,"reversals_pending":str(Decimal(str(tx[3]))+Decimal(str(tx[4]))),"oldest":period[0],"average_delay_days":None,"recommended_action":"Configurer Qonto" if not qonto_available else "Examiner les éléments les plus anciens"},
          "expected_payouts":[{"reference":r[0],"amount":r[1],"date":r[2],"sumup_status":r[3],"status":"SUMUP_ISSUED_BANK_UNAVAILABLE" if not qonto_available else "WAITING_FOR_BANK","bank":None if not qonto_available else "Qonto","received":None,"confidence":"NOT_EVALUATED" if not qonto_available else "PENDING"} for r in pending_rows],
          "payout_status_counts":{"pending":self.db.execute(f"SELECT count(*) FROM sumup_payouts WHERE upper(coalesce(status,'')) IN ({pending_marks})",pending_states).fetchone()[0],"paid":self.db.execute("SELECT count(*) FROM sumup_payouts WHERE upper(coalesce(status,'')) IN ('PAID','COMPLETED','SUCCESSFUL')").fetchone()[0],"unknown":self.db.execute(f"SELECT count(*) FROM sumup_payouts WHERE trim(coalesce(status,''))='' OR upper(status) NOT IN ({pending_marks},'PAID','COMPLETED','SUCCESSFUL','FAILED','CANCELLED','CANCELED')",pending_states).fetchone()[0]},
          "settlement_coverage":{"amount_percent":str(coverage_amount) if coverage_amount is not None else None,"count_percent":str(coverage_count) if coverage_count is not None else None},"anomaly_breakdown":self.anomaly_breakdown(qonto_available),"active_anomalies":active_count,"configuration_alert":configuration,"daily_trends":self.daily_trends()}

    def anomaly_breakdown(self, qonto_available=None):
        """Actionable categories only; connector absence is a single configuration fact."""
        qonto_available=self.qonto_configured if qonto_available is None else qonto_available
        mapping={"NO_CANDIDATE":("Paiement caisse sans SumUp","P1"),"MULTIPLE_CANDIDATES":("Doublon potentiel","P0"),"MULTIPLE_BANK_CANDIDATES":("Mauvais candidat bancaire","P0"),"NO_BANK_CANDIDATE":("Payout sans crédit bancaire","P1")}
        rows=[]
        for method,count,amount,oldest in self.db.execute("SELECT match_method,count(*),sum(CAST(amount_source AS NUMERIC)),min(created_at) FROM payment_settlement_links WHERE status IN ('UNMATCHED','POSSIBLE','CONFLICT') GROUP BY match_method"):
            if method=='NO_BANK_CANDIDATE' and not qonto_available: continue
            label,priority=mapping.get(method,("Rapprochement à vérifier","P2")); rows.append({"category":label,"code":method,"count":count,"amount":str(amount or 0),"oldest":oldest,"priority":priority,"recommended_action":"Ouvrir l’Explorer"})
        return rows

    def daily_trends(self, days=30):
        rows=self.db.execute("SELECT date(timestamp),count(*),sum(CAST(amount AS NUMERIC)),sum(CAST(fee AS NUMERIC)),sum(CAST(amount AS NUMERIC)-CAST(fee AS NUMERIC)-CAST(refunded_amount AS NUMERIC)-CAST(chargeback_amount AS NUMERIC)) FROM sumup_transactions WHERE date(timestamp)>=date('now',?) AND upper(coalesce(status,simple_status,'')) IN ('SUCCESSFUL','SUCCESS','COMPLETED','PAID','VALIDATED') GROUP BY date(timestamp) ORDER BY date(timestamp)",(f'-{int(days)-1} days',)).fetchall()
        return [{"date":r[0],"card_count":r[1],"gross":str(r[2]),"fees":str(r[3]),"net":str(r[4])} for r in rows]
