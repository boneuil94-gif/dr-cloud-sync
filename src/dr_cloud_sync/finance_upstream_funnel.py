"""Sanitized, local-ledger-only evidence for the upstream payment settlement funnel.

This diagnostic deliberately stops at SumUp payout membership.  Payout -> Qonto is
measured by the separate exact finance reconciliation proof, whose semantics must not
be duplicated or weakened here.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3


_REQUIRED_TABLES = {
    "sales",
    "sale_payments",
    "payment_settlement_links",
    "sumup_transactions",
    "sumup_payout_items",
    "sumup_payouts",
}


def _unmeasurable(reason: str) -> dict:
    return {
        "schema_version": 1,
        "evidence_status": "UNMEASURABLE",
        "reason": reason,
        "evidence_scope": "LOCAL_LEDGER_ONLY",
        "provider_exhaustiveness_inferred": False,
        "counts": None,
        "safety": {
            "database_read_only": True,
            "provider_network_calls": False,
            "external_provider_auth": "NONE",
            "mutations": False,
            "row_level_ids_emitted": False,
            "sensitive_values_emitted": False,
        },
    }


def upstream_settlement_funnel(path: Path | str) -> dict:
    """Measure SALE -> PAYMENT -> SUMUP TRANSACTION -> PAYOUT from durable local rows.

    Only the population actually consumed by the current ShopCaisse settlement engine
    is measured: ShopCaisse CARD payments with ``quality_status='VALID'``.  A payment
    advances only through an existing ``MATCHED`` settlement link whose target
    transaction still exists.  Transaction -> payout coverage is accepted only through
    durable ``sumup_payout_items`` membership pointing to an existing payout.

    Multiple MATCHED transaction targets or multiple payout memberships are kept as
    explicit aggregate ambiguity buckets rather than promoted to coverage.
    """
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        try:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not _REQUIRED_TABLES <= tables:
                return _unmeasurable("REQUIRED_LEDGER_MISSING")

            eligible = list(db.execute(
                """SELECT p.payment_id,p.sale_id
                   FROM sale_payments p JOIN sales s ON s.sale_id=p.sale_id
                   WHERE s.source='SHOPCAISSE'
                     AND p.canonical_payment_type='CARD'
                     AND p.quality_status='VALID'"""
            ))
            payment_ids = {row["payment_id"] for row in eligible}
            sale_ids = {row["sale_id"] for row in eligible}

            matched_targets: dict[str, set[str]] = {payment_id: set() for payment_id in payment_ids}
            if payment_ids:
                for row in db.execute(
                    """SELECT l.source_id,l.target_id
                       FROM payment_settlement_links l
                       JOIN sumup_transactions t ON t.sumup_transaction_id=l.target_id
                       WHERE l.source_type='SHOPCAISSE_PAYMENT'
                         AND l.target_type='SUMUP_TRANSACTION'
                         AND l.status='MATCHED'
                         AND l.target_id IS NOT NULL"""
                ):
                    if row["source_id"] in matched_targets:
                        matched_targets[row["source_id"]].add(row["target_id"])

            no_tx = unique_tx = multiple_tx = 0
            unique_target_by_payment: dict[str, str] = {}
            for payment_id, targets in matched_targets.items():
                if not targets:
                    no_tx += 1
                elif len(targets) == 1:
                    unique_tx += 1
                    unique_target_by_payment[payment_id] = next(iter(targets))
                else:
                    multiple_tx += 1

            payout_ids_by_tx: dict[str, set[str]] = {}
            if unique_target_by_payment:
                target_ids = set(unique_target_by_payment.values())
                for row in db.execute(
                    """SELECT i.sumup_transaction_id,i.payout_id
                       FROM sumup_payout_items i
                       JOIN sumup_payouts p ON p.payout_id=i.payout_id
                       WHERE i.sumup_transaction_id IS NOT NULL"""
                ):
                    if row["sumup_transaction_id"] in target_ids:
                        payout_ids_by_tx.setdefault(row["sumup_transaction_id"], set()).add(row["payout_id"])
        except sqlite3.OperationalError:
            return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

        no_payout = unique_payout = multiple_payouts = 0
        for target_id in unique_target_by_payment.values():
            payouts = payout_ids_by_tx.get(target_id, set())
            if not payouts:
                no_payout += 1
            elif len(payouts) == 1:
                unique_payout += 1
            else:
                multiple_payouts += 1

        total = len(payment_ids)
        return {
            "schema_version": 1,
            "evidence_status": "MEASURABLE",
            "evidence_scope": "LOCAL_LEDGER_ONLY",
            "provider_exhaustiveness_inferred": False,
            "counts": {
                "shopcaisse_sales_with_eligible_card_payment": len(sale_ids),
                "eligible_card_payments": total,
                "payments_without_matched_sumup_transaction": no_tx,
                "payments_with_unique_matched_sumup_transaction": unique_tx,
                "payments_with_multiple_matched_sumup_transactions": multiple_tx,
                "unique_transaction_payments_without_payout_membership": no_payout,
                "unique_transaction_payments_with_unique_payout_membership": unique_payout,
                "unique_transaction_payments_with_multiple_payout_memberships": multiple_payouts,
            },
            "coverage": {
                "payment_to_unique_sumup_transaction_ratio": (unique_tx / total) if total else None,
                "unique_transaction_to_unique_payout_ratio": (unique_payout / unique_tx) if unique_tx else None,
                "sale_to_qonto_coverage_claimed": False,
                "downstream_qonto_requires_separate_exact_reconciliation_proof": True,
            },
            "safety": {
                "database_read_only": True,
                "provider_network_calls": False,
                "external_provider_auth": "NONE",
                "mutations": False,
                "row_level_ids_emitted": False,
                "sensitive_values_emitted": False,
            },
        }
    finally:
        db.close()
