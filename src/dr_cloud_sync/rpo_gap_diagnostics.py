"""Sanitized source-aware RPO gap diagnostics from a local recovery watermark.

This module intentionally emits only bounded source/control-plane facts needed to
identify why an RPO source is not comparable. It never emits timestamps, cursors,
errors, provider payloads, credentials or row-level business data.
"""
from __future__ import annotations

from pathlib import Path

from .recovery_watermark import capture_recovery_watermark

_ALLOWED_CLASSIFICATIONS = {
    "ELIGIBLE", "UNMEASURABLE", "INVALID_TIMESTAMP", "NO_DATA",
    "NOT_CONFIGURED", "DISABLED", "DERIVED",
}
_ALLOWED_ORIGINS = {
    "DURABLE_LEDGER", "DATA_SOURCE_CONTROL_PLANE", "DERIVED_FROM_PARENT",
}


def source_rpo_gap_diagnostics(database: Path) -> dict:
    watermark = capture_recovery_watermark(Path(database), captured_from="LOCAL_RPO_GAP_DIAGNOSTIC")
    rows = []
    for source in watermark.get("sources", []):
        classification = str(source.get("classification") or "UNMEASURABLE")
        origin = str(source.get("watermark_origin") or "DATA_SOURCE_CONTROL_PLANE")
        if classification not in _ALLOWED_CLASSIFICATIONS:
            classification = "UNMEASURABLE"
        if origin not in _ALLOWED_ORIGINS:
            origin = "DATA_SOURCE_CONTROL_PLANE"
        records = source.get("records_available")
        if records is None:
            records_state = "UNKNOWN"
        elif isinstance(records, int) and records == 0:
            records_state = "ZERO"
        elif isinstance(records, int) and records > 0:
            records_state = "NONZERO"
        else:
            records_state = "INVALID"
        rows.append({
            "source_id": str(source.get("source_id") or "UNKNOWN")[:80],
            "classification": classification,
            "watermark_origin": origin,
            "records_state": records_state,
            "data_max_state": "PRESENT" if source.get("data_max_at") else "MISSING",
        })
    rows.sort(key=lambda item: item["source_id"])
    gaps = [row for row in rows if row["classification"] in {"UNMEASURABLE", "INVALID_TIMESTAMP"}]
    return {
        "schema_version": 1,
        "evidence_scope": "LOCAL_RECOVERY_WATERMARK_ONLY",
        "provider_exhaustiveness_inferred": False,
        "timestamps_emitted": False,
        "sensitive_values_emitted": False,
        "source_count": len(rows),
        "gap_count": len(gaps),
        "gaps": gaps,
    }
