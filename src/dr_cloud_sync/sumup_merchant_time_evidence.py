"""Read-only aggregate evidence for the final SumUp merchant RPO gap.

This diagnostic inspects only the already-sanitized merchant payload persisted in
SQLite. It never treats local ``imported_at`` as business progress and it does
not authorize a recovery watermark by itself. Raw provider values are consumed
in-memory only to classify timestamp-shape availability.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

_REQUIRED_COLUMNS = {"raw_json", "imported_at"}


def _safety() -> dict:
    return {
        "database_read_only": True,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
        "mutations": False,
        "raw_provider_values_emitted": False,
        "timestamp_values_emitted": False,
        "merchant_identifiers_emitted": False,
        "sensitive_values_emitted": False,
        "imported_at_used_as_business_progress": False,
    }


def _unmeasurable(reason: str) -> dict:
    return {
        "schema_version": 1,
        "evidence_status": "UNMEASURABLE",
        "reason": reason,
        "evidence_scope": "LOCAL_SANITIZED_MERCHANT_PAYLOAD_ONLY",
        "provider_exhaustiveness_inferred": False,
        "rpo_projection_authorized": False,
        "current_endpoint_business_timestamp_semantics_proven": False,
        "counts": None,
        "candidate_readiness": "UNKNOWN",
        "safety": _safety(),
    }


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _locations(payload: dict, key: str) -> list[object]:
    values: list[object] = []
    if key in payload:
        values.append(payload.get(key))
    profile = payload.get("merchant_profile")
    if isinstance(profile, dict) and key in profile:
        values.append(profile.get(key))
    return values


def sumup_merchant_time_evidence(path: Path | str) -> dict:
    """Return bounded aggregate timestamp-shape facts for persisted merchants."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        try:
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "sumup_merchants" not in tables:
                return _unmeasurable("REQUIRED_LEDGER_MISSING")
            columns = {row[1] for row in db.execute("PRAGMA table_info(sumup_merchants)")}
            if not _REQUIRED_COLUMNS <= columns:
                return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")
            rows = db.execute("SELECT raw_json FROM sumup_merchants").fetchall()
        except sqlite3.OperationalError:
            return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

        counts = {
            "merchant_rows": len(rows),
            "raw_json_objects": 0,
            "raw_json_invalid": 0,
            "updated_at_present_rows": 0,
            "updated_at_aware_rows": 0,
            "updated_at_multiple_location_rows": 0,
            "updated_at_conflicting_location_rows": 0,
            "created_at_present_rows": 0,
            "created_at_aware_rows": 0,
        }

        for (raw_json,) in rows:
            try:
                payload = json.loads(raw_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                counts["raw_json_invalid"] += 1
                continue
            if not isinstance(payload, dict):
                counts["raw_json_invalid"] += 1
                continue
            counts["raw_json_objects"] += 1

            updated_values = _locations(payload, "updated_at")
            if updated_values:
                counts["updated_at_present_rows"] += 1
                parsed = [_aware_datetime(value) for value in updated_values]
                valid = [value for value in parsed if value is not None]
                if len(valid) == len(updated_values):
                    counts["updated_at_aware_rows"] += 1
                if len(updated_values) > 1:
                    counts["updated_at_multiple_location_rows"] += 1
                    if len(valid) != len(updated_values) or len(set(valid)) > 1:
                        counts["updated_at_conflicting_location_rows"] += 1

            created_values = _locations(payload, "created_at")
            if created_values:
                counts["created_at_present_rows"] += 1
                parsed = [_aware_datetime(value) for value in created_values]
                if all(value is not None for value in parsed):
                    counts["created_at_aware_rows"] += 1

        merchant_rows = counts["merchant_rows"]
        if merchant_rows == 0:
            readiness = "NO_DATA"
        elif counts["raw_json_invalid"]:
            readiness = "RAW_PAYLOAD_INVALID"
        elif counts["updated_at_conflicting_location_rows"]:
            readiness = "UPDATED_AT_CONFLICTING"
        elif counts["updated_at_aware_rows"] == merchant_rows:
            readiness = "UPDATED_AT_ALL_ROWS_AWARE"
        elif counts["updated_at_present_rows"] == 0:
            readiness = "UPDATED_AT_ABSENT"
        else:
            readiness = "UPDATED_AT_PARTIAL_OR_INVALID"

        return {
            "schema_version": 1,
            "evidence_status": "MEASURABLE",
            "evidence_scope": "LOCAL_SANITIZED_MERCHANT_PAYLOAD_ONLY",
            "provider_exhaustiveness_inferred": False,
            "rpo_projection_authorized": False,
            "current_endpoint_business_timestamp_semantics_proven": False,
            "candidate_readiness": readiness,
            "counts": counts,
            "safety": _safety(),
        }
    finally:
        db.close()
