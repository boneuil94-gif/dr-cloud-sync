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
    counted but never arbitrarily paired. Sign-shape and reference-group aggregation
    evidence are aggregate-only diagnostics and never change reconciliation semantics.
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
            f"WHERE provider=? COLLATE NOCASE AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *eligible_statuses),
        ))

        counts = {
            "payouts_total": len(payouts),
            "payouts_valid_for_amount_gap": 0,
            "valid_payout_amount_positive": 0,
            "valid_payout_amount_zero": 0,
            "valid_payout_amount_negative": 0,
            "payouts_with_reference_currency_overlap": 0,
            "payouts_without_reference_currency_bank_candidate": 0,
            "payouts_with_unique_reference_currency_bank_candidate": 0,
            "payouts_with_multiple_reference_currency_bank_candidates": 0,
            "unique_bank_candidate_amount_positive": 0,
            "unique_bank_candidate_amount_zero": 0,
            "unique_bank_candidate_amount_negative": 0,
            "unique_pairs_amount_equal": 0,
            "unique_pairs_absolute_amount_equal": 0,
            "unique_pairs_bank_amount_lower": 0,
            "unique_pairs_bank_amount_higher": 0,
            "unique_pairs_equal_after_subtracting_payout_fee": 0,
            "unique_pairs_equal_after_adding_payout_fee": 0,
            "unique_pairs_not_explained_by_payout_fee": 0,
            "payouts_with_nonzero_fee": 0,
            "payout_reference_currency_groups_total": 0,
            "payout_reference_currency_groups_single_record": 0,
            "payout_reference_currency_groups_multiple_records": 0,
            "payout_reference_currency_groups_without_bank_candidate": 0,
            "payout_reference_currency_groups_with_unique_bank_candidate": 0,
            "payout_reference_currency_groups_with_multiple_bank_candidates": 0,
            "group_sum_pairs_amount_equal": 0,
            "group_sum_pairs_absolute_amount_equal": 0,
            "group_sum_pairs_bank_amount_lower": 0,
            "group_sum_pairs_bank_amount_higher": 0,
        }

        payout_groups = {}
        credit_groups = {}
        for credit in credits:
            reference = _norm_reference(credit["reference"])
            amount = _money(credit["amount"])
            currency = str(credit["currency"] or "").upper()
            if reference and amount is not None and currency:
                credit_groups.setdefault((reference, currency), []).append(amount)

        for payout in payouts:
            reference = _norm_reference(payout["reference"])
            payout_amount = _money(payout["amount"])
            payout_fee = _money(payout["fee"])
            currency = str(payout["currency"] or "").upper()
            if payout_fee is not None and payout_fee != 0:
                counts["payouts_with_nonzero_fee"] += 1
            if not reference or payout_amount is None or not currency:
                continue
            payout_groups.setdefault((reference, currency), []).append(payout_amount)
            counts["payouts_valid_for_amount_gap"] += 1
            if payout_amount > 0:
                counts["valid_payout_amount_positive"] += 1
            elif payout_amount < 0:
                counts["valid_payout_amount_negative"] += 1
            else:
                counts["valid_payout_amount_zero"] += 1

            candidates = credit_groups.get((reference, currency), [])
            if not candidates:
                counts["payouts_without_reference_currency_bank_candidate"] += 1
                continue
            counts["payouts_with_reference_currency_overlap"] += 1
            if len(candidates) > 1:
                counts["payouts_with_multiple_reference_currency_bank_candidates"] += 1
                continue

            counts["payouts_with_unique_reference_currency_bank_candidate"] += 1
            bank_amount = candidates[0]
            if bank_amount > 0:
                counts["unique_bank_candidate_amount_positive"] += 1
            elif bank_amount < 0:
                counts["unique_bank_candidate_amount_negative"] += 1
            else:
                counts["unique_bank_candidate_amount_zero"] += 1
            if abs(bank_amount) == abs(payout_amount):
                counts["unique_pairs_absolute_amount_equal"] += 1
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

        counts["payout_reference_currency_groups_total"] = len(payout_groups)
        for key, payout_amounts in payout_groups.items():
            if len(payout_amounts) == 1:
                counts["payout_reference_currency_groups_single_record"] += 1
            else:
                counts["payout_reference_currency_groups_multiple_records"] += 1
            candidates = credit_groups.get(key, [])
            if not candidates:
                counts["payout_reference_currency_groups_without_bank_candidate"] += 1
                continue
            if len(candidates) > 1:
                counts["payout_reference_currency_groups_with_multiple_bank_candidates"] += 1
                continue
            counts["payout_reference_currency_groups_with_unique_bank_candidate"] += 1
            payout_sum = sum(payout_amounts)
            bank_amount = candidates[0]
            if abs(bank_amount) == abs(payout_sum):
                counts["group_sum_pairs_absolute_amount_equal"] += 1
            if bank_amount == payout_sum:
                counts["group_sum_pairs_amount_equal"] += 1
            elif bank_amount < payout_sum:
                counts["group_sum_pairs_bank_amount_lower"] += 1
            else:
                counts["group_sum_pairs_bank_amount_higher"] += 1

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
