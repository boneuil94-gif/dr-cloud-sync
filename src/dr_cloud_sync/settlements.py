"""Auditable, read-only links between ShopCaisse payments and SumUp evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3

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


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def match_payment(payment: dict, transactions: list[dict], *, window_seconds=900) -> dict:
    """Pure deterministic matcher. A payment is evaluated independently of its ticket."""
    amount, currency = Decimal(str(payment["amount"])), payment.get("currency") or "EUR"
    occurred = _time(payment["occurred_at"])
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
    return {"status": "MATCHED", "confidence": "1" if exact else "0.82", "match_method": method, "target": target,
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
    def __init__(self, db: sqlite3.Connection, *, window_seconds=900, rounding_tolerance="0.01"):
        self.db = db
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.window_seconds = int(window_seconds)
        self.tolerance = Decimal(str(rounding_tolerance))

    @staticmethod
    def _key(source_type, source_id, target_type, target_id):
        return hashlib.sha256(f"{source_type}:{source_id}:{target_type}:{target_id or '-'}".encode()).hexdigest()

    def recompute(self, *, limit=500, after=None):
        args = [after] if after else []
        clause = "AND p.payment_id>?" if after else ""
        payments = [dict(r) for r in self.db.execute(f"""SELECT p.*,s.sold_at occurred_at,s.currency,s.location
          FROM sale_payments p JOIN sales s ON s.sale_id=p.sale_id
          WHERE s.source='SHOPCAISSE' AND upper(p.payment_type) IN ('CB','CARD','CREDIT_CARD','CARTE') {clause}
          ORDER BY p.payment_id LIMIT ?""", (*args, int(limit)))]
        transactions = [dict(r) for r in self.db.execute("SELECT * FROM sumup_transactions")]
        stamp = datetime.now(timezone.utc).isoformat()
        counts = {x.lower(): 0 for x in ("MATCHED", "POSSIBLE", "UNMATCHED", "CONFLICT")}
        with self.db:
            for payment in payments:
                proposal = match_payment(payment, transactions, window_seconds=self.window_seconds)
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
                proposal=match_payout(payout,credits); target=proposal["target"]
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
        diagnostics = {**total, "period_start": period[0], "period_end": period[1],
                       "shopcaisse_payments": total["analysed"],
                       "sumup_transactions": self.db.execute("SELECT count(*) FROM sumup_transactions").fetchone()[0],
                       "payouts": self.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0]}
        with self.db: self.db.execute("UPDATE payment_settlement_runs SET completed_at=?,cursor=?,diagnostics_json=?,status='COMPLETED' WHERE run_id=?", (datetime.now(timezone.utc).isoformat(), cursor, json.dumps(diagnostics), run_id))
        return {"run_id": run_id, **diagnostics}

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
        return {"link":dict(link),"evidence":[dict(r) for r in self.db.execute("SELECT evidence_type,evidence_json,created_at FROM payment_settlement_evidence WHERE settlement_id=? ORDER BY created_at",(settlement_id,))]}

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
        counts.update({r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM payment_settlement_links GROUP BY status")})
        card_count, card_amount = self.db.execute("""SELECT count(*),coalesce(sum(CAST(p.amount AS NUMERIC)),0) FROM sale_payments p JOIN sales s USING(sale_id) WHERE s.source='SHOPCAISSE' AND upper(p.payment_type) IN ('CB','CARD','CREDIT_CARD','CARTE')""").fetchone()
        matched_amount = self.db.execute("SELECT coalesce(sum(CAST(amount_source AS NUMERIC)),0) FROM payment_settlement_links WHERE status='MATCHED' AND source_type='SHOPCAISSE_PAYMENT'").fetchone()[0]
        payout_states = {"BALANCED": 0, "PARTIAL": 0, "UNBALANCED": 0, "UNAVAILABLE": 0}
        for row in self.db.execute("SELECT payout_id FROM sumup_payouts"):
            payout_states[self.payout(row[0])["balance_status"]] += 1
        has_bank=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_transactions'").fetchone()
        qonto_credits=self.db.execute("SELECT count(*) FROM bank_transactions WHERE provider='Qonto' AND direction='CREDIT'").fetchone()[0] if has_bank else None
        bank_union=" UNION ALL SELECT booked_at FROM bank_transactions WHERE provider='Qonto'" if has_bank else ""
        period=self.db.execute("SELECT min(d),max(d) FROM (SELECT sold_at d FROM sales WHERE source='SHOPCAISSE' UNION ALL SELECT timestamp FROM sumup_transactions UNION ALL SELECT payout_date FROM sumup_payouts"+bank_union+")").fetchone()
        return {"shopcaisse_card_payments": card_count, "shopcaisse_card_amount": str(card_amount), "sumup_transactions": self.db.execute("SELECT count(*) FROM sumup_transactions").fetchone()[0], "payouts": self.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0], "qonto_credits":qonto_credits,"counts": counts, "matched_amount": str(matched_amount), "unmatched_amount": str(Decimal(str(card_amount))-Decimal(str(matched_amount))), "coverage_percent": str((Decimal(str(matched_amount))/Decimal(str(card_amount))*100) if card_amount else None), "payout_balance": payout_states, "period":{"start":period[0],"end":period[1]},"revenue_included": False}
