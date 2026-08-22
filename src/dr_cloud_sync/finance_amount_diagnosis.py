"""Aggregate, read-only diagnosis of the amount stage in exact payout reconciliation.

This helper is intentionally narrower than reconciliation. It only classifies where
payouts that already share an exact normalized reference + currency with an eligible
bank credit stop at the amount stage. It emits counts only: no references, row ids,
free-form banking data or monetary values leave the function.
"""
from __future__ import annotations

from collections import defaultdict
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


def exact_match_amount_diagnosis(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return aggregate amount-stage facts without fuzzy matching or provider access."""
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
            f"WHERE provider = ? COLLATE NOCASE AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *eligible_statuses),
        ))

        bank_amounts_by_reference_currency = defaultdict(list)
        bank_valid = 0
        for row in credits:
            reference = _norm_reference(row["reference"])
            amount = _money(row["amount"])
            currency = str(row["currency"] or "").upper()
            if not reference or amount is None or not currency:
                continue
            bank_valid += 1
            bank_amounts_by_reference_currency[(reference, currency)].append(amount)

        payout_valid = 0
        with_ref_currency_candidate = 0
        single_ref_currency_candidate = 0
        multiple_ref_currency_candidates = 0
        exact_amount_candidate = 0
        sign_inverted_amount_candidate = 0
        different_amount_single_candidate = 0
        different_amount_multiple_candidates = 0

        for row in payouts:
            reference = _norm_reference(row["reference"])
            amount = _money(row["amount"])
            currency = str(row["currency"] or "").upper()
            if not reference or amount is None or not currency:
                continue
            payout_valid += 1
            candidates = bank_amounts_by_reference_currency.get((reference, currency), ())
            if not candidates:
                continue

            with_ref_currency_candidate += 1
            if len(candidates) == 1:
                single_ref_currency_candidate += 1
            else:
                multiple_ref_currency_candidates += 1

            if any(candidate == amount for candidate in candidates):
                exact_amount_candidate += 1
                continue
            if any(abs(candidate) == abs(amount) for candidate in candidates):
                sign_inverted_amount_candidate += 1
                continue
            if len(candidates) == 1:
                different_amount_single_candidate += 1
            else:
                different_amount_multiple_candidates += 1

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
                "payouts_with_reference_currency_candidate": with_ref_currency_candidate,
                "payouts_with_single_reference_currency_candidate": single_ref_currency_candidate,
                "payouts_with_multiple_reference_currency_candidates": multiple_ref_currency_candidates,
                "payouts_with_exact_amount_candidate": exact_amount_candidate,
                "payouts_with_sign_inverted_amount_candidate": sign_inverted_amount_candidate,
                "payouts_with_different_amount_single_candidate": different_amount_single_candidate,
                "payouts_with_different_amount_multiple_candidates": different_amount_multiple_candidates,
            },
        }
    finally:
        db.close()
