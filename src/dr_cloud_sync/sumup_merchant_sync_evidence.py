"""Read-only control-plane evidence for an empty SumUp merchant ledger.

The diagnostic explains scheduler/ledger state without contacting SumUp and
without emitting timestamps, cursors, errors, provider payloads or identifiers.
It cannot authorize an RPO projection or infer provider exhaustiveness.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

_SOURCE_STATUSES = {
    "CONNECTED", "PARTIAL", "NOT_CONFIGURED", "DISABLED", "ERROR",
    "UNSUPPORTED", "UNAVAILABLE",
}
_JOB_STATUSES = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "RETRY"}
_RUN_STATUSES = {"RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "RETRY"}
_REQUIRED_COLUMNS = {
    "data_sources": {
        "source_id", "status", "enabled", "last_success_at", "last_run_id",
        "records_available",
    },
    "sync_jobs": {"job_id", "source_id", "job_type", "status", "attempts"},
    "data_hub_sync_runs": {"run_id", "job_id", "status"},
    "sumup_merchants": {"merchant_code"},
}


def _safety() -> dict:
    return {
        "database_read_only": True,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
        "mutations": False,
        "timestamps_emitted": False,
        "cursor_values_emitted": False,
        "error_values_emitted": False,
        "provider_values_emitted": False,
        "merchant_identifiers_emitted": False,
        "sensitive_values_emitted": False,
        "imported_at_used_as_business_progress": False,
    }


def _unmeasurable(reason: str) -> dict:
    return {
        "schema_version": 1,
        "evidence_status": "UNMEASURABLE",
        "reason": reason,
        "evidence_scope": "LOCAL_DATA_HUB_CONTROL_PLANE_ONLY",
        "provider_exhaustiveness_inferred": False,
        "rpo_projection_authorized": False,
        "diagnosis": "UNKNOWN",
        "control_plane": None,
        "safety": _safety(),
    }


def _bounded(value: object, allowed: set[str]) -> str:
    text = str(value or "").upper()
    return text if text in allowed else "UNKNOWN"


def _presence(value: object) -> str:
    return "PRESENT" if value is not None else "MISSING"


def _count_state(value: object) -> str:
    if value is None:
        return "UNKNOWN"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return "ZERO" if number == 0 else "NONZERO" if number > 0 else "UNKNOWN"


def sumup_merchant_sync_evidence(path: Path | str) -> dict:
    """Explain bounded local control-plane state for ``sync_sumup_merchant``."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        try:
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not set(_REQUIRED_COLUMNS) <= tables:
                return _unmeasurable("REQUIRED_CONTROL_PLANE_MISSING")
            for table, required in _REQUIRED_COLUMNS.items():
                present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if not required <= present:
                    return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

            source = db.execute(
                """SELECT status,enabled,last_success_at,last_run_id,records_available
                   FROM data_sources WHERE source_id='sumup_merchant'"""
            ).fetchone()
            job = db.execute(
                """SELECT status,attempts FROM sync_jobs
                   WHERE job_id='sync_sumup_merchant'
                     AND source_id='sumup_merchant'
                     AND job_type='SUMUP_MERCHANT'"""
            ).fetchone()
            run_rows = db.execute(
                """SELECT status,count(*) AS n FROM data_hub_sync_runs
                   WHERE job_id='sync_sumup_merchant' GROUP BY status"""
            ).fetchall()
            latest = db.execute(
                """SELECT status FROM data_hub_sync_runs
                   WHERE job_id='sync_sumup_merchant' ORDER BY run_id DESC LIMIT 1"""
            ).fetchone()
            merchant_rows = int(db.execute("SELECT count(*) FROM sumup_merchants").fetchone()[0])
        except sqlite3.OperationalError:
            return _unmeasurable("REQUIRED_SCHEMA_INCOMPLETE")

        run_counts = {"total": 0, "succeeded": 0, "failed": 0, "running": 0, "other": 0}
        for row in run_rows:
            status = _bounded(row["status"], _RUN_STATUSES)
            count = max(0, int(row["n"] or 0))
            run_counts["total"] += count
            if status == "SUCCEEDED":
                run_counts["succeeded"] += count
            elif status == "FAILED":
                run_counts["failed"] += count
            elif status == "RUNNING":
                run_counts["running"] += count
            else:
                run_counts["other"] += count

        source_status = _bounded(source["status"], _SOURCE_STATUSES) if source else "UNKNOWN"
        source_enabled = bool(source["enabled"]) if source else False
        job_status = _bounded(job["status"], _JOB_STATUSES) if job else "UNKNOWN"
        latest_status = _bounded(latest["status"], _RUN_STATUSES) if latest else "UNKNOWN"

        if source is None:
            diagnosis = "SOURCE_NOT_REGISTERED"
        elif job is None:
            diagnosis = "JOB_NOT_REGISTERED"
        elif merchant_rows > 0:
            diagnosis = "LEDGER_HAS_DATA"
        elif not source_enabled or source_status not in {"CONNECTED", "UNAVAILABLE"}:
            diagnosis = "SOURCE_NOT_AUTORUNNABLE"
        elif job_status == "BLOCKED":
            diagnosis = "JOB_FAILED_OR_BLOCKED"
        elif run_counts["total"] == 0:
            diagnosis = "JOB_NEVER_RAN"
        elif latest_status == "FAILED" or job_status in {"FAILED", "RETRY"}:
            diagnosis = "JOB_FAILED_OR_BLOCKED"
        elif run_counts["succeeded"] > 0:
            diagnosis = "JOB_SUCCEEDED_NO_LEDGER_ROWS"
        else:
            diagnosis = "CONTROL_PLANE_INDETERMINATE"

        return {
            "schema_version": 1,
            "evidence_status": "MEASURABLE",
            "evidence_scope": "LOCAL_DATA_HUB_CONTROL_PLANE_ONLY",
            "provider_exhaustiveness_inferred": False,
            "rpo_projection_authorized": False,
            "diagnosis": diagnosis,
            "control_plane": {
                "source_present": source is not None,
                "source_status": source_status,
                "source_enabled": source_enabled,
                "source_last_success_presence": _presence(source["last_success_at"]) if source else "MISSING",
                "source_last_run_presence": _presence(source["last_run_id"]) if source else "MISSING",
                "source_records_available_state": _count_state(source["records_available"]) if source else "UNKNOWN",
                "job_present": job is not None,
                "job_status": job_status,
                "job_attempts_state": _count_state(job["attempts"]) if job else "UNKNOWN",
                "latest_run_status": latest_status,
                "run_counts": run_counts,
                "merchant_rows_state": _count_state(merchant_rows),
            },
            "safety": _safety(),
        }
    finally:
        db.close()
