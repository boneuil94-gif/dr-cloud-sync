"""Derived, reproducible SumUp/Qonto reconciliation and anomaly ledger.

Provider ledgers are deliberately read-only here.  Automatic runs only replace
SYSTEM proposals; human decisions are append-only and always take precedence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_reconciliation_runs(
 run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
 status TEXT NOT NULL, diagnostics_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_payout_bank_matches(
 match_id TEXT PRIMARY KEY, payout_id TEXT NOT NULL, bank_transaction_id TEXT,
 status TEXT NOT NULL, confidence TEXT NOT NULL, reasons_json TEXT NOT NULL,
 amount_difference TEXT, date_difference_seconds INTEGER, decision_source TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(payout_id,bank_transaction_id));
CREATE INDEX IF NOT EXISTS idx_fin_match_payout ON finance_payout_bank_matches(payout_id,status);
CREATE INDEX IF NOT EXISTS idx_fin_match_bank ON finance_payout_bank_matches(bank_transaction_id,status);
CREATE TABLE IF NOT EXISTS finance_match_decisions(
 decision_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, decision TEXT NOT NULL,
 decided_at TEXT NOT NULL, decided_by TEXT NOT NULL, old_status TEXT NOT NULL,
 new_status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_anomalies(
 anomaly_id TEXT PRIMARY KEY, type TEXT NOT NULL, severity TEXT NOT NULL,
 source TEXT NOT NULL, related_ids_json TEXT NOT NULL, amount TEXT,
 detected_at TEXT NOT NULL, status TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 resolved_at TEXT);
CREATE INDEX IF NOT EXISTS idx_fin_anomaly_status ON finance_anomalies(status,type);
CREATE TABLE IF NOT EXISTS bank_classifications(
 bank_transaction_id TEXT PRIMARY KEY, classification TEXT NOT NULL,
 confidence TEXT NOT NULL, rule_id TEXT NOT NULL, reason TEXT NOT NULL,
 classified_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_bank_tx_match ON bank_transactions(amount,currency,booked_at,provider,account_id);
CREATE INDEX IF NOT EXISTS idx_sumup_payout_match ON sumup_payouts(amount,currency,payout_date,payout_id);
"""

STATUSES = {"MATCHED", "PROBABLE", "AMBIGUOUS", "UNMATCHED", "REJECTED"}
CLASSIFICATIONS = {"CARD_PAYOUT", "SUPPLIER_PAYMENT", "TAX", "RENT", "SUBSCRIPTION", "TRANSFER", "FEE", "CASH", "OTHER", "UNKNOWN"}


def _now(): return datetime.now(timezone.utc).isoformat()
def _decimal(value):
    try: return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError): return None
def _date(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError): return None
def _id(prefix, *parts): return prefix+":"+hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()


class FinancialReconciliationService:
    """Indexed deterministic matcher with an auditable human-review overlay."""
    def __init__(self, db: sqlite3.Connection, *, window_days=7):
        self.db=db; self.db.row_factory=sqlite3.Row; self.window_seconds=int(window_days)*86400
        self.db.executescript(SCHEMA); self.db.commit()

    def _rows(self, sql, args=()): return [dict(r) for r in self.db.execute(sql,args)]

    def classify_bank(self):
        rules=(
            ("CARD_PAYOUT","bank.sumup.counterparty",Decimal("0.98"),lambda text,direction: direction=="CREDIT" and "sumup" in text),
            ("FEE","bank.fee.label",Decimal("0.95"),lambda text,direction: direction=="DEBIT" and any(x in text for x in ("fee","frais","commission"))),
            ("TAX","bank.tax.label",Decimal("0.93"),lambda text,direction: any(x in text for x in ("impot","impôt","urssaf","tva","dgfip"))),
            ("RENT","bank.rent.label",Decimal("0.92"),lambda text,direction: any(x in text for x in ("loyer","rent"))),
            ("CASH","bank.cash.label",Decimal("0.90"),lambda text,direction: any(x in text for x in ("retrait","cash","especes","espèces"))),
            ("TRANSFER","bank.transfer.label",Decimal("0.85"),lambda text,direction: any(x in text for x in ("virement","transfer"))),
        )
        stamp=_now(); count=0
        with self.db:
            for tx in self._rows("SELECT transaction_id,direction,label,counterparty,reference FROM bank_transactions"):
                text=" ".join(str(tx.get(k) or "") for k in ("label","counterparty","reference")).casefold()
                result=("UNKNOWN",Decimal("0"),"bank.no_reliable_rule","Aucune règle suffisamment fiable")
                for category,rule,confidence,predicate in rules:
                    if predicate(text,tx["direction"]): result=(category,confidence,rule,"Règle déterministe sur les données bancaires"); break
                self.db.execute("INSERT INTO bank_classifications VALUES(?,?,?,?,?,?) ON CONFLICT(bank_transaction_id) DO UPDATE SET classification=excluded.classification,confidence=excluded.confidence,rule_id=excluded.rule_id,reason=excluded.reason,classified_at=excluded.classified_at",(tx["transaction_id"],result[0],str(result[1]),result[2],result[3],stamp)); count+=1
        return count

    def _candidate_rows(self, payout):
        date=_date(payout.get("paid_date") or payout.get("payout_date")); amount=_decimal(payout.get("amount"))
        if date is None or amount is None or not payout.get("currency"): return []
        start=datetime.fromtimestamp(date.timestamp()-self.window_seconds,timezone.utc).isoformat()
        end=datetime.fromtimestamp(date.timestamp()+self.window_seconds,timezone.utc).isoformat()
        # The amount/date/currency index bounds candidate work; no payout×bank scan.
        return self._rows("SELECT * FROM bank_transactions WHERE direction='CREDIT' AND currency=? AND amount=? AND booked_at BETWEEN ? AND ? ORDER BY booked_at,transaction_id",(payout["currency"],str(amount),start,end))

    def _evaluate(self, payout):
        candidates=self._candidate_rows(payout); pid=str(payout["payout_id"])
        if not candidates: return [(None,"UNMATCHED",Decimal("0"),["NO_EXACT_AMOUNT_CURRENCY_DATE_CANDIDATE"],None)]
        evaluated=[]
        pdate=_date(payout.get("paid_date") or payout.get("payout_date")); refs={pid.casefold(),str(payout.get("reference") or "").casefold()}-{""}
        for tx in candidates:
            haystack=" ".join(str(tx.get(k) or "") for k in ("reference","label","counterparty")).casefold()
            reference=any(ref in haystack for ref in refs); sumup="sumup" in haystack
            delta=abs(int((_date(tx["booked_at"])-pdate).total_seconds()))
            reasons=["AMOUNT_EXACT","CURRENCY_EXACT",f"DATE_DELTA_SECONDS={delta}"]
            if reference: reasons.append("PAYOUT_REFERENCE_FOUND")
            if sumup: reasons.append("SUMUP_BANK_EVIDENCE")
            confidence=Decimal("1") if reference else Decimal("0.90") if sumup else Decimal("0.65")
            status="MATCHED" if reference else "PROBABLE"
            evaluated.append((tx,status,confidence,reasons,delta))
        if len(evaluated)>1: evaluated=[(x[0],"AMBIGUOUS",Decimal("0"),x[3]+["MULTIPLE_CANDIDATES"],x[4]) for x in evaluated]
        return evaluated

    def recompute(self):
        stamp=_now(); run_id=_id("finance-run",stamp); seen=set(); counts={k:0 for k in ("MATCHED","PROBABLE","AMBIGUOUS","UNMATCHED")}
        with self.db: self.db.execute("INSERT INTO finance_reconciliation_runs VALUES(?,?,?,?,?)",(run_id,stamp,None,"RUNNING","{}"))
        self.classify_bank()
        payouts=self._rows("SELECT * FROM sumup_payouts ORDER BY payout_id")
        with self.db:
            for payout in payouts:
                for tx,status,confidence,reasons,delta in self._evaluate(payout):
                    bank_id=tx["transaction_id"] if tx else None; match_id=_id("payout-bank",payout["payout_id"],bank_id or "NONE"); seen.add(match_id); counts[status]+=1
                    amount_difference=str((_decimal(tx["amount"])-_decimal(payout["amount"]))) if tx else None
                    # Never overwrite a human decision.
                    existing=self.db.execute("SELECT decision_source FROM finance_payout_bank_matches WHERE match_id=?",(match_id,)).fetchone()
                    if not existing or existing[0]!="HUMAN":
                        self.db.execute("INSERT INTO finance_payout_bank_matches VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET status=excluded.status,confidence=excluded.confidence,reasons_json=excluded.reasons_json,amount_difference=excluded.amount_difference,date_difference_seconds=excluded.date_difference_seconds,updated_at=excluded.updated_at",(match_id,payout["payout_id"],bank_id,status,str(confidence),json.dumps(reasons),amount_difference,delta,"SYSTEM",stamp,stamp))
            # Resolve stale automatic findings rather than deleting history.
            for row in self.db.execute("SELECT match_id FROM finance_payout_bank_matches WHERE decision_source='SYSTEM'").fetchall():
                if row[0] not in seen: self.db.execute("UPDATE finance_payout_bank_matches SET status='UNMATCHED',confidence='0',reasons_json='[\"STALE_AFTER_RECOMPUTE\"]',updated_at=? WHERE match_id=?",(stamp,row[0]))
            self._recompute_anomalies(stamp)
            diagnostics={"payouts_available":len(payouts),"status_counts":counts}
            self.db.execute("UPDATE finance_reconciliation_runs SET completed_at=?,status='COMPLETED',diagnostics_json=? WHERE run_id=?",(stamp,json.dumps(diagnostics),run_id))
        return {"run_id":run_id,**self.evidence()}

    def _anomaly(self, kind,severity,source,ids,amount,stamp):
        aid=_id("finance-anomaly",kind,*ids); payload=json.dumps(list(ids))
        self.db.execute("INSERT INTO finance_anomalies VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(anomaly_id) DO UPDATE SET severity=excluded.severity,related_ids_json=excluded.related_ids_json,amount=excluded.amount,last_seen_at=excluded.last_seen_at,status=CASE WHEN finance_anomalies.status='RESOLVED' THEN 'OPEN' ELSE finance_anomalies.status END,resolved_at=NULL",(aid,kind,severity,source,payload,amount,stamp,"OPEN",stamp))
        return aid

    def _recompute_anomalies(self, stamp):
        seen=set()
        for m in self._rows("SELECT * FROM finance_payout_bank_matches"):
            if m["status"] in {"UNMATCHED","REJECTED"}: seen.add(self._anomaly("PAYOUT_WITHOUT_QONTO","HIGH","SUMUP",[m["payout_id"]],None,stamp))
            elif m["status"]=="AMBIGUOUS": seen.add(self._anomaly("AMBIGUOUS_RECONCILIATION","MEDIUM","RECONCILIATION",[m["payout_id"]],None,stamp))
        for row in self._rows("SELECT b.transaction_id,b.amount FROM bank_transactions b JOIN bank_classifications c ON c.bank_transaction_id=b.transaction_id WHERE c.classification='UNKNOWN'"):
            seen.add(self._anomaly("UNCLASSIFIED_BANK_TRANSACTION","LOW","QONTO",[row["transaction_id"]],row["amount"],stamp))
        for row in self._rows("SELECT transaction_id,amount FROM bank_transactions b WHERE direction='CREDIT' AND NOT EXISTS(SELECT 1 FROM finance_payout_bank_matches m WHERE m.bank_transaction_id=b.transaction_id AND m.status='MATCHED')"):
            seen.add(self._anomaly("BANK_WITHOUT_PAYOUT","LOW","QONTO",[row["transaction_id"]],row["amount"],stamp))
        marks=",".join("?"*len(seen))
        if seen: self.db.execute(f"UPDATE finance_anomalies SET status='RESOLVED',resolved_at=? WHERE status='OPEN' AND anomaly_id NOT IN ({marks})",(stamp,*seen))
        else: self.db.execute("UPDATE finance_anomalies SET status='RESOLVED',resolved_at=? WHERE status='OPEN'",(stamp,))

    def review(self, match_id, decision, user):
        if decision not in {"CONFIRM","REJECT"}: raise ValueError("invalid decision")
        row=self.db.execute("SELECT * FROM finance_payout_bank_matches WHERE match_id=?",(match_id,)).fetchone()
        if not row: raise KeyError(match_id)
        new="MATCHED" if decision=="CONFIRM" else "REJECTED"; stamp=_now(); did=_id("decision",match_id,stamp,user)
        with self.db:
            self.db.execute("INSERT INTO finance_match_decisions VALUES(?,?,?,?,?,?,?)",(did,match_id,decision,stamp,str(user),row["status"],new))
            self.db.execute("UPDATE finance_payout_bank_matches SET status=?,confidence=?,decision_source='HUMAN',updated_at=? WHERE match_id=?",(new,"1" if new=="MATCHED" else "0",stamp,match_id))
        return dict(self.db.execute("SELECT * FROM finance_payout_bank_matches WHERE match_id=?",(match_id,)).fetchone())

    def ledger(self, *, limit=100, offset=0):
        items=self._rows("SELECT b.transaction_id,b.external_transaction_id,b.account_id AS bank_account_id,b.amount,b.currency,b.direction AS side,b.booked_at AS operation_date,b.value_at AS value_date,b.label,b.reference,b.counterparty,b.category AS provider_category,b.provider,b.imported_at,c.classification,c.confidence,c.rule_id,c.reason,c.classified_at FROM bank_transactions b LEFT JOIN bank_classifications c ON c.bank_transaction_id=b.transaction_id ORDER BY b.booked_at DESC LIMIT ? OFFSET ?",(min(max(int(limit),1),500),max(int(offset),0)))
        return {"items":items,"total":self.db.execute("SELECT count(*) FROM bank_transactions").fetchone()[0]}
    def matches(self): return {"items":self._rows("SELECT * FROM finance_payout_bank_matches ORDER BY updated_at DESC")}
    def anomalies(self, status=None):
        where=" WHERE status=?" if status else ""; return {"items":self._rows("SELECT * FROM finance_anomalies"+where+" ORDER BY detected_at DESC",(status,) if status else ())}
    def evidence(self):
        bank=self.db.execute("SELECT count(*) FROM bank_transactions").fetchone()[0]; payouts=self.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0]
        counts={r[0]:r[1] for r in self.db.execute("SELECT status,count(DISTINCT payout_id) FROM finance_payout_bank_matches GROUP BY status")}
        matched=counts.get("MATCHED",0); last=self.db.execute("SELECT max(completed_at) FROM finance_reconciliation_runs WHERE status='COMPLETED'").fetchone()[0]
        return {"bank_transactions_available":bank,"payouts_available":payouts,"payouts_matched":matched,"payouts_probable":counts.get("PROBABLE",0),"payouts_ambiguous":counts.get("AMBIGUOUS",0),"payouts_unmatched":counts.get("UNMATCHED",0),"reconciliation_coverage":{"value":str(Decimal(matched)/Decimal(payouts)) if payouts else None,"numerator":"distinct payouts MATCHED","denominator":"all imported SumUp payouts","payouts":payouts},"last_reconciliation_at":last,"anomalies_open":self.db.execute("SELECT count(*) FROM finance_anomalies WHERE status='OPEN'").fetchone()[0]}
