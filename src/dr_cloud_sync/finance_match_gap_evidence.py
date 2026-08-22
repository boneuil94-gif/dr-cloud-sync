"""Sanitized local-ledger diagnostics for exact payout-to-bank matching gaps.

This module never creates or mutates the ledger and never emits row identifiers,
references, labels, counterparties, or provider payloads. It only counts whether
local SumUp payouts and eligible bank credits overlap on the dimensions already
required by the exact reconciliation contract.
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


def _diagnosis(counts: dict) -> str:
    if counts["payouts_total"] == 0:
        return "NO_LOCAL_SUMUP_PAYOUTS"
    if counts["eligible_credits_total"] == 0:
        return "NO_LOCAL_ELIGIBLE_BANK_CREDITS"
    if counts["exact_triplet_overlap_payouts"]:
        return "EXACT_TRIPLET_OVERLAP_PRESENT"
    if not counts["reference_overlap_payouts"] and not counts["amount_currency_overlap_payouts"]:
        return "NO_REFERENCE_OR_AMOUNT_CURRENCY_OVERLAP"
    if not counts["reference_overlap_payouts"]:
        return "REFERENCE_DOMAIN_GAP"
    if not counts["amount_currency_overlap_payouts"]:
        return "AMOUNT_CURRENCY_DOMAIN_GAP"
    if not counts["reference_amount_overlap_payouts"] and not counts["reference_currency_overlap_payouts"]:
        return "REFERENCE_OVERLAP_BUT_AMOUNT_AND_CURRENCY_GAP"
    return "COMPOSITE_EXACT_MATCH_GAP"


def local_exact_match_gap_evidence(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return aggregate overlap counts for the exact matching dimensions only."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sumup_payouts", "bank_transactions"} <= tables:
            return _unmeasurable("REQUIRED_LEDGER_MISSING")

        payouts = list(db.execute("SELECT amount,currency,reference FROM sumup_payouts"))
        statuses = _eligible_credit_statuses(bank_provider)
        placeholders = ",".join("?" for _ in statuses)
        credits = list(db.execute(
            "SELECT amount,currency,reference FROM bank_transactions "
            f"WHERE provider=? AND direction='CREDIT' AND status IN ({placeholders})",
            (bank_provider, *statuses),
        ))

        normalized_credits = []
        for row in credits:
            normalized_credits.append((
                _norm_reference(row["reference"]),
                _money(row["amount"]),
                str(row["currency"] or "").upper() or None,
            ))

        counts = {
            "payouts_total": len(payouts),
            "eligible_credits_total": len(credits),
            "payouts_with_reference": 0,
            "eligible_credits_with_reference": sum(1 for ref, _, _ in normalized_credits if ref),
            "reference_overlap_payouts": 0,
            "amount_currency_overlap_payouts": 0,
            "reference_amount_overlap_payouts": 0,
            "reference_currency_overlap_payouts": 0,
            "exact_triplet_overlap_payouts": 0,
        }

        for payout in payouts:
            ref = _norm_reference(payout["reference"])
            amount = _money(payout["amount"])
            currency = str(payout["currency"] or "").upper() or None
            if ref:
                counts["payouts_with_reference"] += 1

            ref_overlap = amount_currency_overlap = ref_amount_overlap = ref_currency_overlap = exact = False
            for credit_ref, credit_amount, credit_currency in normalized_credits:
                same_ref = bool(ref and credit_ref and ref == credit_ref)
                same_amount = amount is not None and credit_amount is not None and amount == credit_amount
                same_currency = bool(currency and credit_currency and currency == credit_currency)
                ref_overlap = ref_overlap or same_ref
                amount_currency_overlap = amount_currency_overlap or (same_amount and same_currency)
                ref_amount_overlap = ref_amount_overlap or (same_ref and same_amount)
                ref_currency_overlap = ref_currency_overlap or (same_ref and same_currency)
                exact = exact or (same_ref and same_amount and same_currency)
            counts["reference_overlap_payouts"] += int(ref_overlap)
            counts["amount_currency_overlap_payouts"] += int(amount_currency_overlap)
            counts["reference_amount_overlap_payouts"] += int(ref_amount_overlap)
            counts["reference_currency_overlap_payouts"] += int(ref_currency_overlap)
            counts["exact_triplet_overlap_payouts"] += int(exact)

        return {
            "status": "NO_DATA" if not payouts else "MEASURABLE",
            "reason": None,
            "diagnosis_scope": "LOCAL_LEDGER_ONLY",
            "provider_exhaustiveness_inferred": False,
            "bank_provider": bank_provider,
            "eligible_statuses": list(statuses),
            "matching_contract": {
                "reference": "EXACT_NORMALIZED",
                "amount": "EXACT_DECIMAL",
                "currency": "EXACT",
                "fuzzy_fallback": False,
            },
            "diagnosis": _diagnosis(counts),
            "counts": counts,
            "safety": {
                "database_read_only": True,
                "provider_network_calls": False,
                "mutations": False,
                "row_level_identifiers_emitted": False,
                "reference_values_emitted": False,
            },
        }
    finally:
        db.close()
