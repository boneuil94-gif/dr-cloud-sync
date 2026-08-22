"""Aggregate-only diagnostics for the remaining grouped payout gaps.

This helper reads only the local SQLite ledger in read-only mode. It emits no
reference values, row identifiers, free-form provider payloads or monetary
values and never calls external providers.
"""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from .finance_reconciliation import _eligible_credit_statuses, _money, _norm_reference


KNOWN_PAYOUT_TYPES = {"PAYOUT", "PAYOUT_DEDUCTION"}


def _unmeasurable(reason: str) -> dict:
    return {
        "status": "UNMEASURABLE",
        "reason": reason,
        "diagnosis_scope": "LOCAL_LEDGER_ONLY",
        "provider_exhaustiveness_inferred": False,
        "counts": None,
    }


def _type_bucket(value) -> str:
    normalised = str(value or "").strip().upper()
    if normalised in KNOWN_PAYOUT_TYPES:
        return normalised
    return "MISSING" if not normalised else "OTHER"


def _deductions_state(value) -> str:
    # Missing/blank persisted deduction data is unknown/corrupt evidence, never
    # equivalent to an explicitly persisted empty JSON list.
    if value is None:
        return "INVALID"
    if isinstance(value, (str, bytes)) and not value.strip():
        return "INVALID"
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "INVALID"
    if not isinstance(parsed, list):
        return "INVALID"
    return "NONEMPTY" if parsed else "EMPTY"


def group_composition_gap_funnel(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Classify composition of only the still-unmatched grouped payout gaps.

    A group is in scope only when it has multiple valid SumUp payout rows, one
    unique eligible bank credit, and the exact payout group amount sum differs
    from the bank amount. The diagnostic then counts bounded payout type buckets
    and whether persisted deductions_json is empty/non-empty/invalid. It does not
    inspect or emit deduction contents.
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
            "SELECT amount, currency, reference, type, deductions_json FROM sumup_payouts"
        ))
        eligible_statuses = _eligible_credit_statuses(bank_provider)
        placeholders = ",".join("?" for _ in eligible_statuses)
        credits = list(db.execute(
            "SELECT amount, currency, reference FROM bank_transactions "
            f"WHERE provider=? COLLATE NOCASE AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *eligible_statuses),
        ))

        payout_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        credit_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in payouts:
            reference = _norm_reference(row["reference"])
            currency = str(row["currency"] or "").upper()
            if reference and currency:
                payout_groups.setdefault((reference, currency), []).append(row)
        for row in credits:
            reference = _norm_reference(row["reference"])
            currency = str(row["currency"] or "").upper()
            if reference and currency:
                credit_groups.setdefault((reference, currency), []).append(row)

        counts = {
            "remaining_multi_record_groups_total": 0,
            "remaining_groups_with_payout_type": 0,
            "remaining_groups_with_payout_deduction_type": 0,
            "remaining_groups_with_other_type": 0,
            "remaining_groups_with_missing_type": 0,
            "remaining_groups_with_nonempty_deductions_json": 0,
            "remaining_groups_with_empty_deductions_json": 0,
            "remaining_groups_with_invalid_deductions_json": 0,
            "remaining_payout_rows_total": 0,
            "remaining_payout_rows_type_payout": 0,
            "remaining_payout_rows_type_payout_deduction": 0,
            "remaining_payout_rows_type_other": 0,
            "remaining_payout_rows_type_missing": 0,
        }

        for key, group in payout_groups.items():
            if len(group) < 2:
                continue
            amounts = [_money(row["amount"]) for row in group]
            if any(value is None for value in amounts):
                continue
            candidate_rows = credit_groups.get(key, [])
            if len(candidate_rows) != 1:
                continue
            bank_amount = _money(candidate_rows[0]["amount"])
            if bank_amount is None or bank_amount == sum(amounts):
                continue

            counts["remaining_multi_record_groups_total"] += 1
            counts["remaining_payout_rows_total"] += len(group)
            group_type_buckets = set()
            group_deduction_states = set()
            for row in group:
                bucket = _type_bucket(row["type"])
                group_type_buckets.add(bucket)
                counts[f"remaining_payout_rows_type_{bucket.lower()}"] += 1
                group_deduction_states.add(_deductions_state(row["deductions_json"]))

            for bucket in group_type_buckets:
                counts[f"remaining_groups_with_{bucket.lower()}_type"] += 1
            if "NONEMPTY" in group_deduction_states:
                counts["remaining_groups_with_nonempty_deductions_json"] += 1
            if "EMPTY" in group_deduction_states:
                counts["remaining_groups_with_empty_deductions_json"] += 1
            if "INVALID" in group_deduction_states:
                counts["remaining_groups_with_invalid_deductions_json"] += 1

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
                "deduction_values_emitted": False,
                "free_form_provider_data_emitted": False,
            },
        }
    finally:
        db.close()
