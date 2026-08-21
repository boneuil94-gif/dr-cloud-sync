"""Read-only sanitized evidence for the local bank synchronization control-plane.

This module never contacts the bank provider. It reports only already-persisted
local source/import/diagnostic state and intentionally omits cursors, messages,
request IDs, account identifiers and transaction-level data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness(source, *, now):
    status = str(source["status"] or "")
    if status in {"ERROR", "NOT_CONFIGURED", "DISABLED", "UNSUPPORTED", "UNAVAILABLE"}:
        return status
    last_success = _parse(source["last_success_at"])
    if source["last_run_id"] is None or last_success is None:
        return "CONNECTED_NO_DATA"
    stale_after = max(1, int(source["stale_after_seconds"] or 3600))
    return "FRESH" if now - last_success <= timedelta(seconds=stale_after) else "STALE"


def _cause(source, diagnostic):
    status = str(source["status"] or "")
    if status == "NOT_CONFIGURED":
        return "QONTO_NOT_CONFIGURED"
    if status == "DISABLED":
        return "QONTO_SOURCE_DISABLED"
    if status in {"ERROR", "UNAVAILABLE"}:
        category = str((diagnostic or {}).get("category") or "UNKNOWN").upper()
        if category == "WAF":
            return "QONTO_SYNC_BLOCKED_WAF"
        if category in {"AUTH", "SCOPE"}:
            return "QONTO_SYNC_BLOCKED_AUTH_OR_SCOPE"
        if category in {"NETWORK", "TIMEOUT", "RATE_LIMIT", "HTTP"}:
            return "QONTO_SYNC_BLOCKED_TRANSPORT"
        return "QONTO_SYNC_ERROR_OTHER"
    if source["last_run_id"] is None:
        return "QONTO_NO_SUCCESSFUL_IMPORT_RUN"
    if source["records_available"] is None:
        return "QONTO_IMPORT_COVERAGE_UNKNOWN"
    if int(source["records_available"]) == 0:
        return "QONTO_LOCAL_IMPORT_PROVED_ZERO_RECORDS"
    return "QONTO_LOCAL_RECORDS_AVAILABLE"


def qonto_local_source_evidence(path: Path | str, *, now=None) -> dict:
    """Return aggregate local Qonto source state from SQLite in mode=ro."""
    ledger = Path(path)
    if not ledger.is_file():
        return {"status": "UNMEASURABLE", "reason": "LOCAL_DATABASE_MISSING", "provider_exhaustiveness_inferred": False}
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    db = sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "data_sources" not in tables:
            return {"status": "UNMEASURABLE", "reason": "DATA_SOURCE_STATE_MISSING", "provider_exhaustiveness_inferred": False}
        source = db.execute(
            "SELECT source_id,provider,status,enabled,last_attempt_at,last_success_at,stale_after_seconds,"
            "last_rows_imported,last_run_id,data_min_at,data_max_at,records_available,rows_imported,cursor "
            "FROM data_sources WHERE lower(provider)='qonto' AND (source_id='bank' OR source_type='BANK') "
            "ORDER BY CASE WHEN source_id='bank' THEN 0 ELSE 1 END LIMIT 1"
        ).fetchone()
        if source is None:
            return {"status": "MEASURABLE", "cause": "QONTO_SOURCE_STATE_MISSING", "provider": "Qonto", "provider_exhaustiveness_inferred": False}
        diagnostic = None
        if "connector_diagnostics" in tables:
            row = db.execute(
                "SELECT category,stage,http_status,success,occurred_at FROM connector_diagnostics "
                "WHERE source_id=? AND lower(provider)='qonto' ORDER BY diagnostic_id DESC LIMIT 1",
                (source["source_id"],),
            ).fetchone()
            if row is not None:
                diagnostic = {
                    "category": row["category"],
                    "stage": row["stage"],
                    "http_status": row["http_status"],
                    "success": bool(row["success"]),
                    "occurred_at": row["occurred_at"],
                }
        result = {
            "status": "MEASURABLE",
            "provider": "Qonto",
            "evidence_scope": "LOCAL_SYNC_CONTROL_PLANE_ONLY",
            "provider_exhaustiveness_inferred": False,
            "cause": _cause(source, diagnostic),
            "source": {
                "status": source["status"],
                "enabled": bool(source["enabled"]),
                "freshness": _freshness(source, now=observed),
                "last_attempt_at": source["last_attempt_at"],
                "last_success_at": source["last_success_at"],
                "last_run_id_present": source["last_run_id"] is not None,
                "last_rows_imported": source["last_rows_imported"],
                "rows_imported_total": source["rows_imported"],
                "records_available": source["records_available"],
                "data_min_at": source["data_min_at"],
                "data_max_at": source["data_max_at"],
                "cursor_present": bool(source["cursor"]),
            },
            "latest_diagnostic": diagnostic,
        }
        return result
    finally:
        db.close()
