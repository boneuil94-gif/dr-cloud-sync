"""Read-only sanitized evidence for the local Qonto synchronization control-plane.

No provider call is made. Evidence is restricted to aggregate local control-plane
state; cursors, free-form errors, request IDs and banking identifiers are omitted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

SCOPE = "LOCAL_SYNC_CONTROL_PLANE_ONLY"


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


def _freshness(status, completed_at, stale_after_seconds, *, now):
    status = str(status or "")
    if status in {"ERROR", "NOT_CONFIGURED", "DISABLED", "UNSUPPORTED", "UNAVAILABLE"}:
        return status
    completed = _parse(completed_at)
    if completed is None:
        return "CONNECTED_NO_DATA"
    stale_after = max(1, int(stale_after_seconds or 3600))
    return "FRESH" if now - completed <= timedelta(seconds=stale_after) else "STALE"


def _latest_bank_import(db, source_id):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"sync_jobs", "data_hub_sync_runs"} <= tables:
        return None
    row = db.execute(
        "SELECT r.run_id,r.completed_at,r.result_json "
        "FROM data_hub_sync_runs r JOIN sync_jobs j ON j.job_id=r.job_id "
        "WHERE j.source_id=? AND j.job_type='BANK' AND r.status='SUCCEEDED' "
        "ORDER BY r.run_id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        result = json.loads(row["result_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    return {
        "run_id": row["run_id"],
        "completed_at": row["completed_at"],
        "last_rows_imported": result.get("rows_imported"),
        "records_available": result.get("records_available"),
        "data_min_at": result.get("data_min_at"),
        "data_max_at": result.get("data_max_at"),
    }


def _cause(status, import_run, diagnostic):
    status = str(status or "")
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
    if import_run is None:
        return "QONTO_NO_SUCCESSFUL_IMPORT_RUN"
    records = import_run["records_available"]
    if records is None:
        return "QONTO_IMPORT_COVERAGE_UNKNOWN"
    if int(records) == 0:
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
            "SELECT source_id,provider,status,enabled,stale_after_seconds,rows_imported "
            "FROM data_sources WHERE lower(provider)='qonto' AND (source_id='bank' OR source_type='BANK') "
            "ORDER BY CASE WHEN source_id='bank' THEN 0 ELSE 1 END LIMIT 1"
        ).fetchone()
        if source is None:
            return {"status": "MEASURABLE", "provider": "Qonto", "evidence_scope": SCOPE,
                    "cause": "QONTO_SOURCE_STATE_MISSING", "provider_exhaustiveness_inferred": False}

        diagnostic = None
        if "connector_diagnostics" in tables:
            row = db.execute(
                "SELECT category,stage,http_status,success,occurred_at FROM connector_diagnostics "
                "WHERE source_id=? AND lower(provider)='qonto' ORDER BY diagnostic_id DESC LIMIT 1",
                (source["source_id"],),
            ).fetchone()
            if row is not None:
                diagnostic = {"category": row["category"], "stage": row["stage"],
                              "http_status": row["http_status"], "success": bool(row["success"]),
                              "occurred_at": row["occurred_at"]}

        import_run = _latest_bank_import(db, source["source_id"])
        completed_at = import_run["completed_at"] if import_run else None
        return {
            "status": "MEASURABLE",
            "provider": "Qonto",
            "evidence_scope": SCOPE,
            "provider_exhaustiveness_inferred": False,
            "cause": _cause(source["status"], import_run, diagnostic),
            "source": {
                "status": source["status"],
                "enabled": bool(source["enabled"]),
                "freshness": _freshness(source["status"], completed_at, source["stale_after_seconds"], now=observed),
                "successful_import_run_present": import_run is not None,
                "last_import_completed_at": completed_at,
                "last_rows_imported": import_run["last_rows_imported"] if import_run else None,
                "rows_imported_total": source["rows_imported"],
                "records_available": import_run["records_available"] if import_run else None,
                "data_min_at": import_run["data_min_at"] if import_run else None,
                "data_max_at": import_run["data_max_at"] if import_run else None,
            },
            "latest_diagnostic": diagnostic,
        }
    finally:
        db.close()
