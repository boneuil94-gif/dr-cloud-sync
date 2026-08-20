"""Read-only, fail-closed reconciliation between SumUp payouts and bank credits.

A payout is only MATCHED when one and only one booked bank credit has the same
currency, exact decimal amount and exact normalized provider reference. Missing,
malformed or contended matches remain unresolved/ambiguous; no fuzzy matching is used.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import sqlite3
from pathlib import Path


def _norm_reference(value: object) -> str | None:
    text = " ".join(str(value or "").strip().upper().split())
    return text or None


def _money(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _unmeasurable(reason: str) -> dict:
    return {
        "status": "UNMEASURABLE",
        "reason": reason,
        "payouts_total": None,
        "matched": None,
        "unresolved": None,
        "ambiguous": None,
    }


def reconcile_sumup_payouts_to_bank(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return sanitized reconciliation facts without mutating or creating a ledger."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    # mode=ro enforces the function's read-only contract at SQLite level.
    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"sumup_payouts", "bank_transactions"}
        if not required <= tables:
            return _unmeasurable("REQUIRED_LEDGER_MISSING")

        payouts = list(db.execute(
            "SELECT payout_id, amount, currency, reference, status FROM sumup_payouts ORDER BY payout_id"
        ))
        credits = list(db.execute(
            "SELECT transaction_id, amount, currency, reference, status FROM bank_transactions "
            "WHERE provider=? AND direction='CREDIT' AND status='BOOKED' ORDER BY transaction_id",
            (bank_provider,),
        ))

        provisional = []
        single_candidate_ids = []
        for payout in payouts:
            reference = _norm_reference(payout["reference"])
            amount = _money(payout["amount"])
            currency = str(payout["currency"] or "").upper()
            if not reference:
                provisional.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "PAYOUT_REFERENCE_MISSING"})
                continue
            if amount is None or not currency:
                provisional.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "PAYOUT_AMOUNT_OR_CURRENCY_INVALID"})
                continue

            candidates = []
            for bank in credits:
                bank_amount = _money(bank["amount"])
                if bank_amount is None or bank_amount != amount:
                    continue
                if str(bank["currency"] or "").upper() != currency:
                    continue
                if _norm_reference(bank["reference"]) != reference:
                    continue
                candidates.append(bank["transaction_id"])

            if len(candidates) == 1:
                candidate = candidates[0]
                single_candidate_ids.append(candidate)
                provisional.append({"payout_id": payout["payout_id"], "status": "CANDIDATE", "bank_transaction_id": candidate})
            elif len(candidates) > 1:
                provisional.append({"payout_id": payout["payout_id"], "status": "AMBIGUOUS", "reason": "MULTIPLE_EXACT_BANK_MATCHES"})
            else:
                provisional.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "NO_EXACT_BANK_MATCH"})

        contention = Counter(single_candidate_ids)
        matched = unresolved = ambiguous = 0
        rows = []
        for row in provisional:
            if row["status"] == "CANDIDATE":
                candidate = row["bank_transaction_id"]
                if contention[candidate] == 1:
                    row = {"payout_id": row["payout_id"], "status": "MATCHED", "bank_transaction_id": candidate}
                    matched += 1
                else:
                    row = {"payout_id": row["payout_id"], "status": "AMBIGUOUS", "reason": "BANK_CREDIT_CONTENDED"}
                    ambiguous += 1
            elif row["status"] == "AMBIGUOUS":
                ambiguous += 1
            else:
                unresolved += 1
            rows.append(row)

        total = len(payouts)
        return {
            "status": "NO_DATA" if total == 0 else "MEASURABLE",
            "payouts_total": total,
            "matched": matched,
            "unresolved": unresolved,
            "ambiguous": ambiguous,
            "coverage_ratio": (matched / total) if total else None,
            "rows": rows,
        }
    finally:
        db.close()
