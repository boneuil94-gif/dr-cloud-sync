"""Aggregate-only diagnostics for the remaining grouped SumUp payout amount gaps.

This module is evidence-only. It reads the local SQLite ledger in read-only mode,
never calls providers, never mutates business data, and emits no reference values,
row identifiers or monetary values.
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


def group_fee_gap_funnel(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Classify grouped payout gaps against aggregate payout fees, exactly.

    Only same-reference, same-currency multi-record payout groups with exactly one
    eligible bank credit are considered for amount relationships. Invalid amounts
    or fees make the corresponding exact diagnostic ineligible rather than causing
    a partial sum. No tolerance, date window or fuzzy fallback is used.
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

        payouts = list(db.execute("SELECT amount, currency, fee, reference FROM sumup_payouts"))
        eligible_statuses = _eligible_credit_statuses(bank_provider)
        placeholders = ",".join("?" for _ in eligible_statuses)
        credits = list(db.execute(
            "SELECT amount, currency, reference FROM bank_transactions "
            f"WHERE provider=? COLLATE NOCASE AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *eligible_statuses),
        ))

        payout_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        credit_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for payout in payouts:
            reference = _norm_reference(payout["reference"])
            currency = str(payout["currency"] or "").upper()
            if reference and currency:
                payout_groups.setdefault((reference, currency), []).append(payout)
        for credit in credits:
            reference = _norm_reference(credit["reference"])
            currency = str(credit["currency"] or "").upper()
            if reference and currency:
                credit_groups.setdefault((reference, currency), []).append(credit)

        counts = {
            "payout_reference_currency_groups_total": len(payout_groups),
            "multi_record_groups_total": 0,
            "multi_record_groups_without_bank_candidate": 0,
            "multi_record_groups_with_unique_bank_candidate": 0,
            "multi_record_groups_with_multiple_bank_candidates": 0,
            "multi_record_groups_with_invalid_bank_candidate_amount": 0,
            "unique_candidate_groups_with_invalid_payout_amount": 0,
            "unique_candidate_groups_with_invalid_payout_fee": 0,
            "unique_candidate_groups_exact_amount_equal": 0,
            "unique_candidate_groups_bank_amount_higher": 0,
            "unique_candidate_groups_bank_amount_lower": 0,
            "unique_candidate_groups_with_nonzero_total_fee": 0,
            "unique_candidate_groups_equal_after_adding_total_fee": 0,
            "unique_candidate_groups_equal_after_subtracting_total_fee": 0,
            "unique_candidate_groups_not_explained_by_total_fee": 0,
        }

        for key, group in payout_groups.items():
            if len(group) < 2:
                continue
            counts["multi_record_groups_total"] += 1
            candidate_rows = credit_groups.get(key, [])
            if not candidate_rows:
                counts["multi_record_groups_without_bank_candidate"] += 1
                continue
            candidate_amounts = [_money(row["amount"]) for row in candidate_rows]
            if any(value is None for value in candidate_amounts):
                counts["multi_record_groups_with_invalid_bank_candidate_amount"] += 1
                continue
            if len(candidate_rows) > 1:
                counts["multi_record_groups_with_multiple_bank_candidates"] += 1
                continue
            counts["multi_record_groups_with_unique_bank_candidate"] += 1

            amounts = [_money(row["amount"]) for row in group]
            fees = [_money(row["fee"]) for row in group]
            if any(value is None for value in amounts):
                counts["unique_candidate_groups_with_invalid_payout_amount"] += 1
                continue

            payout_sum = sum(amounts)
            bank_amount = candidate_amounts[0]
            if bank_amount == payout_sum:
                counts["unique_candidate_groups_exact_amount_equal"] += 1
                continue
            if bank_amount > payout_sum:
                counts["unique_candidate_groups_bank_amount_higher"] += 1
            else:
                counts["unique_candidate_groups_bank_amount_lower"] += 1

            if any(value is None for value in fees):
                counts["unique_candidate_groups_with_invalid_payout_fee"] += 1
                continue
            fee_sum = sum(fees)
            if fee_sum != 0:
                counts["unique_candidate_groups_with_nonzero_total_fee"] += 1
            explained = False
            if bank_amount == payout_sum + fee_sum:
                counts["unique_candidate_groups_equal_after_adding_total_fee"] += 1
                explained = True
            if bank_amount == payout_sum - fee_sum:
                counts["unique_candidate_groups_equal_after_subtracting_total_fee"] += 1
                explained = True
            if not explained:
                counts["unique_candidate_groups_not_explained_by_total_fee"] += 1

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
