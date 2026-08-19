#!/usr/bin/env bash
set -Eeuo pipefail

PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || { echo PRODUCTION_DATA_PROOF_PYTHON_MISSING >&2; exit 127; }

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"

# Keep this proof on the same deployment-environment contract as every other
# production Compose caller. The dedicated contract test verifies ordering.
compose_subcommand=compose

# This proof reads only the durable SQLite database already mounted by the
# production application. It never calls ShopCaisse, PrestaShop, SumUp or Qonto.
docker "$compose_subcommand" exec -T drcloud-os python - <<'PY'
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
from pathlib import Path


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


def table_count(db, tables, name):
    if name not in tables:
        return None
    return int(db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])


def match_statuses(db, tables, name):
    if name not in tables:
        return None
    columns = {row[1] for row in db.execute(f'PRAGMA table_info("{name}")')}
    column = next((candidate for candidate in ("status", "confidence") if candidate in columns), None)
    if column is None:
        return {"status": "SCHEMA_UNMEASURABLE", "counts": {}}
    counts = {}
    for status, total in db.execute(f'SELECT "{column}", count(*) FROM "{name}" GROUP BY "{column}"'):
        counts[str(status or "UNKNOWN").upper()] = int(total)
    return {"status": "MEASURED", "field": column, "counts": counts}


data_dir = Path(os.environ.get("DRCLOUD_DATA_DIR", "/data"))
database = data_dir / "drcloud.db"
if not database.is_file():
    raise SystemExit("PRODUCTION_DATA_DATABASE_UNAVAILABLE")

with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    quick_check = db.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise SystemExit("PRODUCTION_DATA_DATABASE_INTEGRITY_FAILED")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    job_counts = {}
    if "sync_jobs" in tables:
        job_counts = {
            str(source_id): int(total)
            for source_id, total in db.execute(
                "SELECT source_id, count(*) FROM sync_jobs GROUP BY source_id"
            )
        }

    safe_source_columns = (
        "source_id", "source_type", "provider", "status", "enabled",
        "last_success_at", "stale_after_seconds", "data_min_at", "data_max_at",
        "records_available", "rows_imported",
    )
    sources = []
    source_plane = "UNAVAILABLE"
    if "data_sources" in tables:
        available = {row[1] for row in db.execute("PRAGMA table_info(data_sources)")}
        if set(safe_source_columns) <= available:
            source_plane = "AVAILABLE"
            rows = db.execute(
                f"SELECT {','.join(safe_source_columns)} FROM data_sources ORDER BY source_id"
            ).fetchall()
            for row in rows:
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                item["registered_jobs"] = job_counts.get(str(item["source_id"]), 0)
                for key in ("last_success_at", "data_min_at", "data_max_at"):
                    if item[key] is not None:
                        item[key] = canonical_time(item[key])

                status = str(item.get("status") or "UNKNOWN").upper()
                provider = str(item.get("provider") or "UNKNOWN").upper()
                rows_imported = int(item.get("rows_imported") or 0)
                records = item.get("records_available")
                external_unwired = provider not in {"LOCAL", "NONE"} and item["registered_jobs"] == 0

                if not item["enabled"]:
                    classification = "DISABLED"
                elif status == "NOT_CONFIGURED":
                    classification = "NOT_CONFIGURED"
                elif external_unwired:
                    classification = "UNWIRED"
                elif status == "ERROR":
                    classification = "ERROR"
                elif rows_imported <= 0 and records is not None and int(records) <= 0:
                    classification = "NO_DATA"
                elif rows_imported <= 0 and item.get("data_max_at") is None:
                    classification = "WIRED_NO_LOCAL_DATA"
                elif item.get("data_max_at") is None:
                    classification = "LOCAL_ROWS_MEASURED_TIMESTAMP_UNKNOWN"
                else:
                    classification = "LOCAL_DATA_MEASURED"

                # rows_imported and data_max_at are local durable facts. They are
                # not upstream authority totals, so local presence must never be
                # promoted to exhaustive provider coverage.
                item["classification"] = classification
                if classification in {"DISABLED", "NOT_CONFIGURED", "UNWIRED", "NO_DATA"}:
                    coverage_status = "NOT_APPLICABLE"
                elif classification == "ERROR":
                    coverage_status = "UNAVAILABLE"
                else:
                    coverage_status = "UNKNOWN_AUTHORITY_TOTAL"
                item["coverage_status"] = coverage_status
                sources.append(item)
        else:
            source_plane = "SCHEMA_INCOMPLETE"

    funnel_tables = (
        ("sales", "sales"),
        ("payments", "sale_payments"),
        ("sumup_transactions", "sumup_transactions"),
        ("sumup_payouts", "sumup_payouts"),
        ("qonto_transactions", "bank_transactions"),
    )
    funnel = {
        stage: {
            "count": table_count(db, tables, table),
            "authority": "LOCAL_LEDGER_COUNT" if table in tables else "UNAVAILABLE",
        }
        for stage, table in funnel_tables
    }
    reconciliation = {
        name: match_statuses(db, tables, name)
        for name in ("payment_matches", "finance_reconciliation_matches")
    }

configured = [
    row for row in sources
    if row["enabled"] and str(row.get("status") or "").upper() != "NOT_CONFIGURED"
]
wired = [row for row in configured if row["classification"] != "UNWIRED"]
unwired = [row for row in configured if row["classification"] == "UNWIRED"]
local_measured = [
    row for row in wired
    if row["classification"] in {"LOCAL_DATA_MEASURED", "LOCAL_ROWS_MEASURED_TIMESTAMP_UNKNOWN"}
]
unknown_authority = [
    row for row in wired if row["coverage_status"] == "UNKNOWN_AUTHORITY_TOTAL"
]
known_funnel = sum(item["count"] is not None for item in funnel.values())

report = {
    "schema_version": 2,
    "environment": "production",
    "result": "PRODUCTION_DATA_PROOF_CAPTURED",
    "evidence_level": "PRODUCTION_READ_ONLY_LOCAL_LEDGER_FACTS",
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "database": {"quick_check": quick_check, "mode": "READ_ONLY"},
    "source_control_plane": {
        "status": source_plane,
        "source_count": len(sources),
        "configured_sources": len(configured),
        "operationally_wired_sources": len(wired),
        "unwired_sources": len(unwired),
        "unwired_source_ids": [row["source_id"] for row in unwired],
        "local_data_measured_sources": len(local_measured),
        "unknown_authority_total_sources": len(unknown_authority),
        "authoritative_coverage_proven": False,
        "sources": sources,
    },
    "finance_funnel": {
        "stages": funnel,
        "known_local_stage_counts": known_funnel,
        "end_to_end_match_rate": None,
        "end_to_end_status": "NOT_PROVEN",
        "reconciliation": reconciliation,
    },
    "safety": {
        "database_read_only": True,
        "mutations": False,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
    },
}

# Final fail-closed evidence scan. Source/provider identifiers are retained, but
# no cursor, errors, raw payloads, credentials or PII fields are selected above.
forbidden_keys = (
    "cursor", "last_error", "raw", "password", "secret", "token",
    "credential", "api_key", "authorization", "email", "phone",
)
forbidden_value = re.compile(r"(?i)(bearer |basic |-----begin|gh[pousr]_|sk_live_|AKIA[0-9A-Z]{16})")

def scan(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(marker in str(key).lower() for marker in forbidden_keys):
                raise SystemExit("PRODUCTION_DATA_EVIDENCE_SENSITIVE_KEY")
            scan(child)
    elif isinstance(value, list):
        for child in value:
            scan(child)
    elif isinstance(value, str) and forbidden_value.search(value):
        raise SystemExit("PRODUCTION_DATA_EVIDENCE_SENSITIVE_VALUE")

scan(report)
print(json.dumps(report, indent=2, sort_keys=True))
PY
