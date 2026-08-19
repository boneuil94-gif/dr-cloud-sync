#!/usr/bin/env bash
set -Eeuo pipefail

PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || { echo SUMUP_PAYOUT_DIAGNOSTIC_PYTHON_MISSING >&2; exit 127; }

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"
compose_subcommand=compose

# Read-only forensic capture: no SumUp request and no provider credentials are
# passed to the Python process. Stored messages/payloads/cursors are deliberately
# excluded even though connector_diagnostics already sanitises them on write.
docker "$compose_subcommand" exec -T drcloud-os python - <<'PY'
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
from pathlib import Path

SOURCE_ID = "sumup_payouts"
SAFE_SOURCE_COLUMNS = (
    "source_id", "source_type", "provider", "status", "enabled",
    "last_attempt_at", "last_success_at", "rows_imported", "records_available",
    "data_min_at", "data_max_at",
)
SAFE_DIAGNOSTIC_COLUMNS = (
    "diagnostic_id", "source_id", "provider", "job_id", "run_id", "operation",
    "stage", "endpoint_path", "http_status", "category", "exception_type",
    "attempt", "occurred_at", "duration_ms", "next_retry_at", "success",
)


def canonical_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def table_columns(db, table):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return set()
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


data_dir = Path(os.environ.get("DRCLOUD_DATA_DIR", "/data"))
database = data_dir / "drcloud.db"
if not database.is_file():
    raise SystemExit("SUMUP_PAYOUT_DIAGNOSTIC_DATABASE_UNAVAILABLE")

with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    quick_check = db.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise SystemExit("SUMUP_PAYOUT_DIAGNOSTIC_DATABASE_INTEGRITY_FAILED")

    source_columns = table_columns(db, "data_sources")
    if not set(SAFE_SOURCE_COLUMNS) <= source_columns:
        raise SystemExit("SUMUP_PAYOUT_DIAGNOSTIC_SOURCE_SCHEMA_UNAVAILABLE")
    source = db.execute(
        f"SELECT {','.join(SAFE_SOURCE_COLUMNS)} FROM data_sources WHERE source_id=?",
        (SOURCE_ID,),
    ).fetchone()
    if source is None:
        raise SystemExit("SUMUP_PAYOUT_SOURCE_NOT_FOUND")
    source = dict(source)
    source["enabled"] = bool(source["enabled"])
    for key in ("last_attempt_at", "last_success_at", "data_min_at", "data_max_at"):
        if source.get(key) is not None:
            source[key] = canonical_time(source[key])

    diagnostic_columns = table_columns(db, "connector_diagnostics")
    diagnostics = []
    diagnostic_status = "UNAVAILABLE"
    if set(SAFE_DIAGNOSTIC_COLUMNS) <= diagnostic_columns:
        diagnostic_status = "AVAILABLE"
        rows = db.execute(
            f"SELECT {','.join(SAFE_DIAGNOSTIC_COLUMNS)} FROM connector_diagnostics "
            "WHERE source_id=? ORDER BY diagnostic_id DESC LIMIT 10",
            (SOURCE_ID,),
        ).fetchall()
        last_success = source.get("last_success_at")
        for row in rows:
            item = dict(row)
            item["success"] = bool(item["success"])
            for key in ("occurred_at", "next_retry_at"):
                if item.get(key) is not None:
                    item[key] = canonical_time(item[key])
            occurred = item.get("occurred_at")
            item["historical"] = bool(
                not item["success"] and last_success and occurred and occurred < last_success
            )
            item["current"] = bool(not item["success"] and not item["historical"])
            diagnostics.append(item)

current_failures = [row for row in diagnostics if row["current"]]
latest_current = current_failures[0] if current_failures else None
report = {
    "schema_version": 1,
    "environment": "production",
    "result": "SUMUP_PAYOUT_DIAGNOSTIC_CAPTURED",
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "database": {"mode": "READ_ONLY", "quick_check": quick_check},
    "source": source,
    "diagnostics": {
        "status": diagnostic_status,
        "rows_returned": len(diagnostics),
        "current_failure_count": len(current_failures),
        "latest_current": latest_current,
        "recent": diagnostics,
    },
    "safety": {
        "database_read_only": True,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
        "mutations": False,
        "raw_messages_selected": False,
        "response_excerpts_selected": False,
        "cursors_selected": False,
    },
}

forbidden_keys = (
    "message", "response_excerpt", "cursor", "request_id", "password", "secret",
    "token", "credential", "api_key", "authorization", "cookie", "email", "phone",
)
forbidden_value = re.compile(r"(?i)(bearer |basic |-----begin|gh[pousr]_|sk_live_|AKIA[0-9A-Z]{16})")

def scan(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(marker in str(key).lower() for marker in forbidden_keys):
                raise SystemExit("SUMUP_PAYOUT_DIAGNOSTIC_SENSITIVE_KEY")
            scan(child)
    elif isinstance(value, list):
        for child in value:
            scan(child)
    elif isinstance(value, str) and forbidden_value.search(value):
        raise SystemExit("SUMUP_PAYOUT_DIAGNOSTIC_SENSITIVE_VALUE")

scan(report)
print(json.dumps(report, indent=2, sort_keys=True))
PY
