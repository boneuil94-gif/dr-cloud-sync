"""Sanitized local shape evidence for unresolved ShopCaisse payment signals.

Diagnostic only: raw tender values are processed in-memory to derive aggregate
cardinality/dominance facts and are never returned, logged, or persisted.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sqlite3
import unicodedata

_REQUIRED_TABLES = {"sales", "sale_payments"}
_REQUIRED_COLUMNS = {
    "sales": {"sale_id", "source"},
    "sale_payments": {
        "sale_id", "payment_type", "name", "description",
        "canonical_payment_type", "mapping_rule", "mapping_version",
    },
}
_CURRENT_MAPPING_VERSION = "shopcaisse-payment-types-v2"


def _unmeasurable(reason: str) -> dict:
    return {
        "schema_version": 1,
        "evidence_status": "UNMEASURABLE",
        "reason": reason,
        "evidence_scope": "LOCAL_LEDGER_ONLY",
        "provider_exhaustiveness_inferred": False,
        "counts": None,
        "safety": _safety(),
    }


def _safety() -> dict:
    return {
        "database_read_only": True,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
        "mutations": False,
        "raw_payment_values_emitted": False,
        "provider_values_emitted": False,
        "row_level_ids_emitted": False,
        "sensitive_values_emitted": False,
    }


def _shape_key(value: object) -> str:
    """Normalize only for aggregate shape comparison, never classification."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return " ".join(text.split())


def upstream_unknown_payment_signal_shape(path: Path | str) -> dict:
    """Describe unresolved current-mapper signals without exposing their values."""
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
                """SELECT p.payment_type,p.name,p.description
                   FROM sale_payments p JOIN sales s ON s.sale_id=p.sale_id
                   WHERE s.source='SHOPCAISSE'
                     AND COALESCE(p.canonical_payment_type,'') NOT IN
                         ('CARD','CASH','BANK_TRANSFER','VOUCHER','GIFT_CARD','STORE_CREDIT','OTHER')
                     AND p.mapping_version=? AND p.mapping_rule='unknown-label'""",
                (_CURRENT_MAPPING_VERSION,),
            ).fetchall()
        except sqlite3.OperationalError:
            return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

        type_counts: Counter[str] = Counter()
        name_counts: Counter[str] = Counter()
        pair_counts: Counter[tuple[str, str]] = Counter()
        total = equal = different = description_present = 0
        for row in rows:
            payment_type = _shape_key(row["payment_type"])
            name = _shape_key(row["name"])
            description = _shape_key(row["description"])
            # #238 proved raw signal presence separately; this diagnostic still
            # fails closed if a selected unknown row lacks both primary signals.
            if not payment_type and not name:
                return _unmeasurable("UNKNOWN_SIGNAL_MISSING")
            total += 1
            description_present += int(bool(description))
            type_counts[payment_type] += 1
            name_counts[name] += 1
            pair_counts[(payment_type, name)] += 1
            if payment_type == name:
                equal += 1
            else:
                different += 1

        largest_pair = max(pair_counts.values(), default=0)
        largest_type = max(type_counts.values(), default=0)
        largest_name = max(name_counts.values(), default=0)
        counts = {
            "unknown_current_mapping_rows": total,
            "unknown_payment_type_name_equal": equal,
            "unknown_payment_type_name_different": different,
            "unknown_description_present": description_present,
            "unknown_distinct_payment_type_signatures": len(type_counts),
            "unknown_distinct_name_signatures": len(name_counts),
            "unknown_distinct_pair_signatures": len(pair_counts),
            "unknown_largest_payment_type_bucket": largest_type,
            "unknown_largest_name_bucket": largest_name,
            "unknown_largest_pair_bucket": largest_pair,
            "unknown_rows_outside_largest_pair_bucket": total - largest_pair,
        }
        return {
            "schema_version": 1,
            "evidence_status": "MEASURABLE",
            "evidence_scope": "LOCAL_LEDGER_ONLY",
            "provider_exhaustiveness_inferred": False,
            "counts": counts,
            "safety": _safety(),
        }
    finally:
        db.close()
