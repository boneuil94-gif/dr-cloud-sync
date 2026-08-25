"""Sanitized local evidence explaining the upstream settlement population.

This diagnostic does not change settlement semantics.  It only classifies the local
ShopCaisse payment population consumed by ``upstream_settlement_funnel`` so an empty
funnel is not confused with proven provider absence.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3


_REQUIRED_TABLES = {"sales", "sale_payments", "sales_sync_states"}
_EXPOSURE = {"EXPOSED", "API_NOT_EXPOSED", "NOT_OBSERVED"}
_KNOWN_NON_CARD = {"CASH", "BANK_TRANSFER", "VOUCHER", "GIFT_CARD", "STORE_CREDIT", "OTHER"}


def _unmeasurable(reason: str) -> dict:
    return {
        "schema_version": 1,
        "evidence_status": "UNMEASURABLE",
        "reason": reason,
        "evidence_scope": "LOCAL_LEDGER_ONLY",
        "provider_exhaustiveness_inferred": False,
        "counts": None,
        "diagnostics": None,
        "safety": {
            "database_read_only": True,
            "provider_network_calls": False,
            "external_provider_auth": "NONE",
            "mutations": False,
            "provider_values_emitted": False,
            "row_level_ids_emitted": False,
            "sensitive_values_emitted": False,
        },
    }


def _presence(value) -> str:
    if value is None:
        return "UNKNOWN"
    try:
        return "NONZERO" if int(value) > 0 else "ZERO"
    except (TypeError, ValueError):
        return "UNKNOWN"


def upstream_payment_population(path: Path | str) -> dict:
    """Classify ShopCaisse payment rows using bounded local-only buckets."""
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

            sales_total = db.execute("SELECT count(*) FROM sales WHERE source='SHOPCAISSE'").fetchone()[0]
            sales_with_payment = db.execute(
                """SELECT count(DISTINCT s.sale_id)
                   FROM sales s JOIN sale_payments p ON p.sale_id=s.sale_id
                   WHERE s.source='SHOPCAISSE'"""
            ).fetchone()[0]
            rows = db.execute(
                """SELECT p.canonical_payment_type,p.quality_status,count(*) AS n
                   FROM sale_payments p JOIN sales s ON s.sale_id=p.sale_id
                   WHERE s.source='SHOPCAISSE'
                   GROUP BY p.canonical_payment_type,p.quality_status"""
            ).fetchall()
            sync = db.execute(
                "SELECT last_report_json FROM sales_sync_states WHERE source='SHOPCAISSE'"
            ).fetchone()
        except sqlite3.OperationalError:
            return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

        counts = {
            "shopcaisse_sales": int(sales_total),
            "shopcaisse_sales_with_any_payment": int(sales_with_payment),
            "shopcaisse_payments": 0,
            "shopcaisse_payments_card": 0,
            "shopcaisse_payments_non_card_known": 0,
            "shopcaisse_payments_unknown_or_missing_type": 0,
            "shopcaisse_payments_quality_valid": 0,
            "shopcaisse_payments_quality_non_valid": 0,
            "shopcaisse_payments_card_and_valid": 0,
            "shopcaisse_payments_card_and_non_valid": 0,
        }
        for row in rows:
            canonical = str(row["canonical_payment_type"] or "").upper()
            quality = str(row["quality_status"] or "").upper()
            n = int(row["n"])
            counts["shopcaisse_payments"] += n
            if canonical == "CARD":
                counts["shopcaisse_payments_card"] += n
                counts["shopcaisse_payments_card_and_valid" if quality == "VALID" else "shopcaisse_payments_card_and_non_valid"] += n
            elif canonical in _KNOWN_NON_CARD:
                counts["shopcaisse_payments_non_card_known"] += n
            else:
                counts["shopcaisse_payments_unknown_or_missing_type"] += n
            counts["shopcaisse_payments_quality_valid" if quality == "VALID" else "shopcaisse_payments_quality_non_valid"] += n

        exposure = "UNKNOWN"
        ticket_presence = payment_object_presence = "UNKNOWN"
        if sync and sync["last_report_json"]:
            try:
                report = json.loads(sync["last_report_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                report = None
            if isinstance(report, dict):
                raw_exposure = str(report.get("shopcaisse_payments") or "").upper()
                exposure = raw_exposure if raw_exposure in _EXPOSURE else "UNKNOWN"
                ticket_presence = _presence(report.get("tickets_observed"))
                payment_object_presence = _presence(report.get("payment_objects_observed"))

        return {
            "schema_version": 1,
            "evidence_status": "MEASURABLE",
            "evidence_scope": "LOCAL_LEDGER_ONLY",
            "provider_exhaustiveness_inferred": False,
            "counts": counts,
            "diagnostics": {
                "shopcaisse_payment_exposure": exposure,
                "tickets_observed_presence": ticket_presence,
                "payment_objects_observed_presence": payment_object_presence,
            },
            "safety": {
                "database_read_only": True,
                "provider_network_calls": False,
                "external_provider_auth": "NONE",
                "mutations": False,
                "provider_values_emitted": False,
                "row_level_ids_emitted": False,
                "sensitive_values_emitted": False,
            },
        }
    finally:
        db.close()
