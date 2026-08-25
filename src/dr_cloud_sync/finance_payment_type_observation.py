"""Sanitized local evidence for ShopCaisse payment-type observation and mapping.

This module is diagnostic only. It never changes payment classification or
settlement semantics, never calls ShopCaisse, and never emits raw tender labels.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3


_REQUIRED_TABLES = {"sales", "sale_payments"}
_REQUIRED_COLUMNS = {
    "sales": {"sale_id", "source"},
    "sale_payments": {
        "sale_id", "payment_type", "name", "description",
        "canonical_payment_type", "mapping_rule", "mapping_version",
    },
}
_CURRENT_MAPPING_VERSION = "shopcaisse-payment-types-v2"
_KNOWN_NON_CARD = {"CASH", "BANK_TRANSFER", "VOUCHER", "GIFT_CARD", "STORE_CREDIT", "OTHER"}


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
            "raw_payment_values_emitted": False,
            "provider_values_emitted": False,
            "row_level_ids_emitted": False,
            "sensitive_values_emitted": False,
        },
    }


def _rule_bucket(rule: object) -> str:
    value = str(rule or "")
    if value == "missing":
        return "mapping_rule_missing"
    if value == "unknown-label":
        return "mapping_rule_unknown_label"
    if value.startswith("exact-normalized:payment_type:"):
        return "mapping_rule_recognized_payment_type"
    if value.startswith("exact-normalized:name:"):
        return "mapping_rule_recognized_name"
    if value.startswith("exact-normalized:description:"):
        return "mapping_rule_recognized_description"
    return "mapping_rule_other_or_legacy"


def _present(value: object) -> bool:
    return bool(str(value or "").strip())


def upstream_payment_type_observation(path: Path | str) -> dict:
    """Measure raw-signal presence vs canonical mapping without exposing labels."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        try:
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not _REQUIRED_TABLES <= tables:
                return _unmeasurable("REQUIRED_LEDGER_MISSING")
            for table, required in _REQUIRED_COLUMNS.items():
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if not required <= columns:
                    return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")
            rows = db.execute(
                """SELECT p.payment_type,p.name,p.description,p.canonical_payment_type,
                          p.mapping_rule,p.mapping_version
                   FROM sale_payments p JOIN sales s ON s.sale_id=p.sale_id
                   WHERE s.source='SHOPCAISSE'"""
            ).fetchall()
        except sqlite3.OperationalError:
            return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

        counts = {
            "shopcaisse_payments": 0,
            "canonical_card": 0,
            "canonical_known_non_card": 0,
            "canonical_unknown_or_other": 0,
            "raw_signal_none": 0,
            "raw_signal_any": 0,
            "raw_payment_type_present": 0,
            "raw_name_present": 0,
            "raw_description_present": 0,
            "unknown_with_no_raw_signal": 0,
            "unknown_with_raw_signal": 0,
            "mapping_rule_missing": 0,
            "mapping_rule_unknown_label": 0,
            "mapping_rule_recognized_payment_type": 0,
            "mapping_rule_recognized_name": 0,
            "mapping_rule_recognized_description": 0,
            "mapping_rule_other_or_legacy": 0,
            "mapping_version_current": 0,
            "mapping_version_other_or_legacy": 0,
            "unknown_current_mapping_version": 0,
            "unknown_other_or_legacy_mapping_version": 0,
        }
        for row in rows:
            counts["shopcaisse_payments"] += 1
            type_present = _present(row["payment_type"])
            name_present = _present(row["name"])
            description_present = _present(row["description"])
            raw_any = type_present or name_present or description_present
            counts["raw_signal_any" if raw_any else "raw_signal_none"] += 1
            counts["raw_payment_type_present"] += int(type_present)
            counts["raw_name_present"] += int(name_present)
            counts["raw_description_present"] += int(description_present)

            canonical = str(row["canonical_payment_type"] or "")
            if canonical == "CARD":
                counts["canonical_card"] += 1
            elif canonical in _KNOWN_NON_CARD:
                counts["canonical_known_non_card"] += 1
            else:
                counts["canonical_unknown_or_other"] += 1
                counts["unknown_with_raw_signal" if raw_any else "unknown_with_no_raw_signal"] += 1

            counts[_rule_bucket(row["mapping_rule"])] += 1
            current = str(row["mapping_version"] or "") == _CURRENT_MAPPING_VERSION
            counts["mapping_version_current" if current else "mapping_version_other_or_legacy"] += 1
            if canonical not in {"CARD", *_KNOWN_NON_CARD}:
                counts["unknown_current_mapping_version" if current else "unknown_other_or_legacy_mapping_version"] += 1

        return {
            "schema_version": 1,
            "evidence_status": "MEASURABLE",
            "evidence_scope": "LOCAL_LEDGER_ONLY",
            "provider_exhaustiveness_inferred": False,
            "counts": counts,
            "safety": {
                "database_read_only": True,
                "provider_network_calls": False,
                "external_provider_auth": "NONE",
                "mutations": False,
                "raw_payment_values_emitted": False,
                "provider_values_emitted": False,
                "row_level_ids_emitted": False,
                "sensitive_values_emitted": False,
            },
        }
    finally:
        db.close()
