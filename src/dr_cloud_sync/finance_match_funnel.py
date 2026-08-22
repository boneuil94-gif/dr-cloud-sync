"""Aggregate, read-only diagnostics for the exact SumUp payout -> bank-credit match funnel.

The helper explains where exact reconciliation stops without emitting references,
row identifiers or free-form banking data. It never contacts a provider and opens
the SQLite ledger in read-only mode.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sqlite3

from .finance_reconciliation import _eligible_credit_statuses, _money, _norm_reference


def _unmeasurable(reason: str) -> dict:
    return {
        "status": "UNMEASURABLE",
        "reason": reason,
        "diagnosis_scope": "LOCAL_LEDGER_ONLY",
        "provider_exhaustiveness_inferred": False,
        "counts": None,
    }


def exact_match_funnel(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return aggregate funnel counts for the existing exact-match contract only."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sumup_payouts", "bank_transactions"} <= tables:
            return _unmeasurable("REQUIRED_LEDGER_MISSING")

        payouts = list(db.execute("SELECT amount, currency, reference FROM sumup_payouts"))
        eligible_statuses = _eligible_credit_statuses(bank_provider)
        placeholders = ",".join("?" for _ in eligible_statuses)
        credits = list(db.execute(
            "SELECT amount, currency, reference FROM bank_transactions "
            f"WHERE provider=? AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *eligible_statuses),
        ))

        bank_ref = Counter()
        bank_ref_currency = Counter()
        bank_exact = Counter()
        bank_valid = 0
        for row in credits:
            reference = _norm_reference(row["reference"])
            amount = _money(row["amount"])
            currency = str(row["currency"] or "").upper()
            if not reference or amount is None or not currency:
                continue
            bank_valid += 1
            bank_ref[reference] += 1
            bank_ref_currency[(reference, currency)] += 1
            bank_exact[(reference, currency, amount)] += 1

        payout_valid = 0
        reference_overlap = 0
        reference_currency_overlap = 0
        exact_tuple_overlap = 0
        exact_tuple_unique = 0
        exact_tuple_multiple = 0
        for row in payouts:
            reference = _norm_reference(row["reference"])
            amount = _money(row["amount"])
            currency = str(row["currency"] or "").upper()
            if not reference or amount is None or not currency:
                continue
            payout_valid += 1
            if bank_ref[reference]:
                reference_overlap += 1
            if bank_ref_currency[(reference, currency)]:
                reference_currency_overlap += 1
            candidates = bank_exact[(reference, currency, amount)]
            if candidates:
                exact_tuple_overlap += 1
            if candidates == 1:
                exact_tuple_unique += 1
            elif candidates > 1:
                exact_tuple_multiple += 1

        return {
            "status": "NO_DATA" if not payouts else "MEASURABLE",
            "reason": None,
            "diagnosis_scope": "LOCAL_LEDGER_ONLY",
            "provider_exhaustiveness_inferred": False,
            "bank_provider": str(bank_provider),
            "eligible_statuses": list(eligible_statuses),
            "counts": {
                "payouts_total": len(payouts),
                "payouts_valid_for_exact_matching": payout_valid,
                "eligible_bank_credits_total": len(credits),
                "eligible_bank_credits_valid_for_exact_matching": bank_valid,
                "payouts_with_reference_overlap": reference_overlap,
                "payouts_with_reference_currency_overlap": reference_currency_overlap,
                "payouts_with_exact_tuple_overlap": exact_tuple_overlap,
                "payouts_with_unique_exact_bank_candidate": exact_tuple_unique,
                "payouts_with_multiple_exact_bank_candidates": exact_tuple_multiple,
            },
        }
    finally:
        db.close()
