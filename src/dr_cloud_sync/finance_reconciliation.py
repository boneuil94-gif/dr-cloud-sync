"""Read-only, fail-closed reconciliation between SumUp payouts and bank credits.

A payout is only MATCHED when one and only one booked bank credit has the same
currency, exact decimal amount and exact normalized provider reference. Missing
or ambiguous references remain explicitly unresolved; no fuzzy matching is used.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import sqlite3
from pathlib import Path


def _norm_reference(value: object) -> str | None:
    text = " ".join(str(value or "").strip().upper().split())
    return text or None


def _money(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def reconcile_sumup_payouts_to_bank(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return sanitized reconciliation facts without mutating the durable ledger."""
    db = sqlite3.connect(Path(path))
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"sumup_payouts", "bank_transactions"}
        if not required <= tables:
            return {
                "status": "UNMEASURABLE",
                "reason": "REQUIRED_LEDGER_MISSING",
                "payouts_total": None,
                "matched": None,
                "unresolved": None,
                "ambiguous": None,
            }

        payouts = list(db.execute(
            "SELECT payout_id, amount, currency, reference, status FROM sumup_payouts ORDER BY payout_id"
        ))
        credits = list(db.execute(
            "SELECT transaction_id, amount, currency, reference, status FROM bank_transactions "
            "WHERE provider=? AND direction='CREDIT' AND status='BOOKED' ORDER BY transaction_id",
            (bank_provider,),
        ))

        matched = unresolved = ambiguous = 0
        rows = []
        for payout in payouts:
            reference = _norm_reference(payout["reference"])
            amount = _money(payout["amount"])
            currency = str(payout["currency"] or "").upper()
            if not reference:
                unresolved += 1
                rows.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "PAYOUT_REFERENCE_MISSING"})
                continue
            if amount is None or not currency:
                unresolved += 1
                rows.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "PAYOUT_AMOUNT_OR_CURRENCY_INVALID"})
                continue

            candidates = []
            for bank in credits:
                bank_amount = _money(bank["amount"])
                if bank_amount != amount:
                    continue
                if str(bank["currency"] or "").upper() != currency:
                    continue
                if _norm_reference(bank["reference"]) != reference:
                    continue
                candidates.append(bank["transaction_id"])

            if len(candidates) == 1:
                matched += 1
                rows.append({"payout_id": payout["payout_id"], "status": "MATCHED", "bank_transaction_id": candidates[0]})
            elif len(candidates) > 1:
                ambiguous += 1
                rows.append({"payout_id": payout["payout_id"], "status": "AMBIGUOUS", "reason": "MULTIPLE_EXACT_BANK_MATCHES"})
            else:
                unresolved += 1
                rows.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "NO_EXACT_BANK_MATCH"})

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
