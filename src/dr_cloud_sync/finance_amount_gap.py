"""Sanitized local-ledger evidence for exact-match amount gaps.

This helper never reconciles by approximation. It only explains, in aggregate, why
reference+currency candidates fail the exact amount gate. No provider calls, writes,
row identifiers, reference values or monetary values are emitted.
"""
from __future__ import annotations

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


def amount_gap_funnel(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return aggregate-only evidence for the amount stage of exact reconciliation.

    Amount relationships are evaluated only when a payout has exactly one bank credit
    candidate with the same normalized reference and currency. Multiple candidates are
    counted but never arbitrarily paired.
    """
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sumup_payouts", "bank_transactions"} <= tables:
            return _unmeasurable("REQUIRED_LEDGER_MISSING")

        payouts = list(db.execute(
            "SELECT amount, currency, fee, reference FROM sumup_payouts"
        ))
        eligible_statuses = _eligible_credit_statuses(bank_provider)
        placeholders = ",".join("?" for _ in eligible_statuses)
        credits = list(db.execute(
            "SELECT amount, currency, reference FROM bank_transactions "
            f"WHERE provider=? AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *eligible_statuses),
        ))

        counts = {
            "payouts_total": len(payouts),
            "payouts_valid_for_amount_gap": 0,
            "payouts_with_reference_currency_overlap": 0,
            "payouts_without_reference_currency_bank_candidate": 0,
            "payouts_with_unique_reference_currency_bank_candidate": 0,
            "payouts_with_multiple_reference_currency_bank_candidates": 0,
            "unique_pairs_amount_equal": 0,
            "unique_pairs_bank_amount_lower": 0,
            "unique_pairs_bank_amount_higher": 0,
            "unique_pairs_equal_after_subtracting_payout_fee": 0,
            "unique_pairs_equal_after_adding_payout_fee": 0,
            "unique_pairs_not_explained_by_payout_fee": 0,
            "payouts_with_nonzero_fee": 0,
        }

        for payout in payouts:
            reference = _norm_reference(payout["reference"])
            payout_amount = _money(payout["amount"])
            payout_fee = _money(payout["fee"])
            currency = str(payout["currency"] or "").upper()
            if payout_fee is not None and payout_fee != 0:
                counts["payouts_with_nonzero_fee"] += 1
            if not reference or payout_amount is None or not currency:
                continue
            counts["payouts_valid_for_amount_gap"] += 1

            candidates = []
            for credit in credits:
                if str(credit["currency"] or "").upper() != currency:
                    continue
                if _norm_reference(credit["reference"]) != reference:
                    continue
                credit_amount = _money(credit["amount"])
                if credit_amount is not None:
                    candidates.append(credit_amount)

            if not candidates:
                counts["payouts_without_reference_currency_bank_candidate"] += 1
                continue
            counts["payouts_with_reference_currency_overlap"] += 1
            if len(candidates) > 1:
                counts["payouts_with_multiple_reference_currency_bank_candidates"] += 1
                continue

            counts["payouts_with_unique_reference_currency_bank_candidate"] += 1
            bank_amount = candidates[0]
            if bank_amount == payout_amount:
                counts["unique_pairs_amount_equal"] += 1
                continue
            if bank_amount < payout_amount:
                counts["unique_pairs_bank_amount_lower"] += 1
            else:
                counts["unique_pairs_bank_amount_higher"] += 1

            explained = False
            if payout_fee is not None and payout_fee != 0:
                if bank_amount == payout_amount - payout_fee:
                    counts["unique_pairs_equal_after_subtracting_payout_fee"] += 1
                    explained = True
                if bank_amount == payout_amount + payout_fee:
                    counts["unique_pairs_equal_after_adding_payout_fee"] += 1
                    explained = True
            if not explained:
                counts["unique_pairs_not_explained_by_payout_fee"] += 1

        return {
            "status": "NO_DATA" if not payouts else "MEASURABLE",
            "reason": None,
            "diagnosis_scope": "LOCAL_LEDGER_ONLY",
            "bank_provider": bank_provider,
            "eligible_statuses": list(eligible_statuses),
            "provider_exhaustiveness_inferred": False,
            "counts": counts,
            "safety": {
                "database_read_only": True,
                "provider_network_calls": False,
                "mutations": False,
                "reference_values_emitted": False,
                "row_level_identifiers_emitted": False,
                "monetary_values_emitted": False,
            },
        }
    finally:
        db.close()
