"""Sanitised, source-aware recovery watermarks and comparisons.

Only ``data_sources`` is consulted.  This deliberately excludes technical
tables and makes the business-data contract explicit and reviewable.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
SAFE_COLUMNS = ("source_id", "source_type", "provider", "status", "enabled",
                "last_success_at", "stale_after_seconds", "data_min_at",
                "data_max_at", "records_available")


def _iso(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _classification(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    if not row.get("enabled"):
        return "DISABLED"
    if status in {"NOT_CONFIGURED", "UNCONFIGURED"}:
        return "NOT_CONFIGURED"
    records = row.get("records_available")
    if records is None:
        return "UNMEASURABLE"
    if records <= 0:
        return "NO_DATA"
    if row.get("data_max_at") and not _iso(row["data_max_at"]):
        return "INVALID_TIMESTAMP"
    if not row.get("data_max_at"):
        return "UNMEASURABLE"
    return "ELIGIBLE"


def capture_recovery_watermark(database: Path, *, captured_at=None,
                               captured_from: str = "LIVE_DATABASE") -> dict:
    """Read a SQLite database read-only and return only approved source fields."""
    when = _iso(captured_at) if captured_at is not None else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if when is None:
        raise ValueError("captured_at must be an ISO-8601 timestamp with timezone")
    rows = []
    table_available = False
    try:
        with sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True) as db:
            table_available = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_sources'"
            ).fetchone() is not None
            if table_available:
                columns = {r[1] for r in db.execute("PRAGMA table_info(data_sources)")}
                if set(SAFE_COLUMNS) <= columns:
                    db.row_factory = sqlite3.Row
                    rows = [dict(r) for r in db.execute(
                        f"SELECT {','.join(SAFE_COLUMNS)} FROM data_sources ORDER BY source_id")]
    except sqlite3.Error:
        table_available = False

    sources = []
    for row in rows:
        clean = {key: row.get(key) for key in SAFE_COLUMNS}
        clean["enabled"] = bool(clean["enabled"])
        clean["classification"] = _classification(clean)
        # Canonicalise valid timestamps; preserve invalid values solely so the
        # explicit INVALID_TIMESTAMP classification remains auditable.
        for key in ("last_success_at", "data_min_at", "data_max_at"):
            if clean[key] is not None and _iso(clean[key]):
                clean[key] = _iso(clean[key])
        sources.append(clean)
    eligible = [s for s in sources if s["classification"] not in {"DISABLED", "NOT_CONFIGURED", "NO_DATA"}]
    measured = [s for s in eligible if s["classification"] == "ELIGIBLE"]
    missing = len(eligible) - len(measured)
    aggregate = max((s["data_max_at"] for s in measured), default=None)
    confidence = "MEDIUM" if eligible and not missing else "LOW" if measured else "UNKNOWN"
    return {"schema_version": SCHEMA_VERSION, "captured_from": captured_from,
            "captured_at": when, "aggregate_data_max_at": aggregate,
            "confidence": confidence, "table_available": table_available,
            "coverage": {"eligible_sources": len(eligible),
                         "measured_sources": len(measured),
                         "missing_data_max_sources": missing}, "sources": sources}


def validate_recovery_watermark(value: object) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return False
    if not _iso(value.get("captured_at")):
        return False
    aggregate = value.get("aggregate_data_max_at")
    if aggregate is not None and not _iso(aggregate):
        return False
    return isinstance(value.get("coverage"), dict) and isinstance(value.get("sources"), list)


def compare_recovery_watermarks(live: dict, backup: dict) -> dict:
    """Compare business progress; the maximum per-source lag is observed RPO.

    Count growth without comparable timestamps is reported, never converted to
    a zero-second RPO.  NOT_CONFIGURED, DISABLED and NO_DATA sources are neutral.
    """
    base = {"comparable_sources": 0, "unmeasurable_sources": 0,
            "business_data_gap_seconds": None, "sync_progress_gap_seconds": None,
            "record_count_gap": 0, "observed_rpo_seconds": None,
            "confidence": "UNKNOWN"}
    if not (validate_recovery_watermark(live) and validate_recovery_watermark(backup)):
        return base
    ignored = {"DISABLED", "NOT_CONFIGURED", "NO_DATA"}
    live_sources = {s.get("source_id"): s for s in live["sources"] if s.get("classification") not in ignored}
    old_sources = {s.get("source_id"): s for s in backup["sources"] if s.get("classification") not in ignored}
    gaps, sync_gaps = [], []
    for source_id, current in live_sources.items():
        old = old_sources.get(source_id)
        current_count, old_count = current.get("records_available"), old.get("records_available") if old else None
        if isinstance(current_count, int) and isinstance(old_count, int):
            base["record_count_gap"] += max(0, current_count - old_count)
        current_at = _iso(current.get("data_max_at")); old_at = _iso(old.get("data_max_at")) if old else None
        if current_at and old_at:
            delta = max(0, (datetime.fromisoformat(current_at.replace("Z", "+00:00")) -
                            datetime.fromisoformat(old_at.replace("Z", "+00:00"))).total_seconds())
            gaps.append(delta); base["comparable_sources"] += 1
            live_sync = _iso(current.get("last_success_at")); old_sync = _iso(old.get("last_success_at"))
            if live_sync and old_sync:
                sync_gaps.append(max(0, (datetime.fromisoformat(live_sync.replace("Z", "+00:00")) - datetime.fromisoformat(old_sync.replace("Z", "+00:00"))).total_seconds()))
        else:
            base["unmeasurable_sources"] += 1
    if gaps:
        base["business_data_gap_seconds"] = base["observed_rpo_seconds"] = max(gaps)
        base["sync_progress_gap_seconds"] = max(sync_gaps) if sync_gaps else None
        total = len(live_sources)
        base["confidence"] = "HIGH" if base["comparable_sources"] == total and not base["unmeasurable_sources"] else "MEDIUM"
    return base
