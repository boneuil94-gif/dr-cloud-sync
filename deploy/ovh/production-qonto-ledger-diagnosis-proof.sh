#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"

expected_sha="${EXPECTED_DEPLOYED_SHA:-}"
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || { echo QONTO_LEDGER_DIAGNOSIS_EXPECTED_SHA_INVALID >&2; exit 64; }
deployed_sha="$(git -C "$repo" rev-parse HEAD)"
[[ "$deployed_sha" == "$expected_sha" ]] || { echo QONTO_LEDGER_DIAGNOSIS_DEPLOYED_SHA_MISMATCH >&2; exit 65; }

source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"
compose_subcommand=compose

# Read only the already-persisted production SQLite control plane and bank ledger.
# This proof performs no Qonto network call and uses no Qonto credential.
docker "$compose_subcommand" exec -T \
  -e EXPECTED_DEPLOYED_SHA="$expected_sha" \
  drcloud-os python - <<'PY'
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

expected_sha = os.environ.get("EXPECTED_DEPLOYED_SHA", "")
if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
    raise SystemExit("QONTO_LEDGER_DIAGNOSIS_EXPECTED_SHA_INVALID")

data_dir = Path(os.environ.get("DRCLOUD_DATA_DIR", "/data"))
database = data_dir / "drcloud.db"
if not database.is_file():
    raise SystemExit("QONTO_LEDGER_DIAGNOSIS_DATABASE_UNAVAILABLE")

with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SystemExit("QONTO_LEDGER_DIAGNOSIS_DATABASE_INTEGRITY_FAILED")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "bank_transactions" not in tables:
        raise SystemExit("QONTO_LEDGER_DIAGNOSIS_BANK_LEDGER_MISSING")

    rows = list(db.execute(
        "SELECT direction,status,booked_at,imported_at,reference FROM bank_transactions WHERE lower(provider)=lower(?)",
        ("Qonto",),
    ))
    total = len(rows)
    directions = Counter(str(row["direction"] or "UNKNOWN").upper() for row in rows)
    statuses = Counter(str(row["status"] or "UNKNOWN").upper() for row in rows)
    credits = [row for row in rows if str(row["direction"] or "").upper() == "CREDIT"]
    booked_credits = [row for row in credits if str(row["status"] or "").upper() == "BOOKED"]
    credit_statuses = Counter(str(row["status"] or "UNKNOWN").upper() for row in credits)

    def canonical_time(value):
        if not value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    def bound(items, field, fn):
        values = [canonical_time(row[field]) for row in items]
        values = [value for value in values if value is not None]
        return fn(values) if values else None

    source_control = None
    if "data_sources" in tables:
        available = {row[1] for row in db.execute("PRAGMA table_info(data_sources)")}
        safe = {"source_id","source_type","provider","status","enabled","last_success_at","stale_after_seconds","data_min_at","data_max_at","records_available","rows_imported"}
        if safe <= available:
            source_rows = list(db.execute(
                "SELECT source_id,source_type,provider,status,enabled,last_success_at,stale_after_seconds,data_min_at,data_max_at,records_available,rows_imported "
                "FROM data_sources WHERE lower(provider)=lower(?) ORDER BY source_id",
                ("Qonto",),
            ))
            source_control = []
            for row in source_rows:
                source_control.append({
                    "source_id": row["source_id"],
                    "source_type": row["source_type"],
                    "provider": row["provider"],
                    "status": row["status"],
                    "enabled": bool(row["enabled"]),
                    "last_success_at": canonical_time(row["last_success_at"]),
                    "stale_after_seconds": row["stale_after_seconds"],
                    "data_min_at": canonical_time(row["data_min_at"]),
                    "data_max_at": canonical_time(row["data_max_at"]),
                    "records_available": row["records_available"],
                    "rows_imported": row["rows_imported"],
                })

if total == 0:
    diagnosis = "NO_LOCAL_QONTO_TRANSACTIONS"
elif not credits:
    diagnosis = "NO_LOCAL_QONTO_CREDITS"
elif not booked_credits:
    diagnosis = "NO_LOCAL_QONTO_BOOKED_CREDITS"
else:
    diagnosis = "LOCAL_QONTO_BOOKED_CREDITS_PRESENT"

report = {
    "schema_version": 1,
    "environment": "production",
    "result": "PRODUCTION_QONTO_LEDGER_DIAGNOSIS_CAPTURED",
    "evidence_level": "PRODUCTION_READ_ONLY_LOCAL_LEDGER_FACTS",
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "deployed_sha": expected_sha,
    "diagnosis_scope": "LOCAL_LEDGER_ONLY",
    "provider_exhaustiveness_inferred": False,
    "diagnosis": diagnosis,
    "bank_ledger": {
        "provider": "Qonto",
        "transactions_total": total,
        "direction_counts": dict(sorted(directions.items())),
        "status_counts": dict(sorted(statuses.items())),
        "credits_total": len(credits),
        "credit_status_counts": dict(sorted(credit_statuses.items())),
        "booked_credits_total": len(booked_credits),
        "booked_at_min": bound(rows, "booked_at", min),
        "booked_at_max": bound(rows, "booked_at", max),
        "latest_imported_at": bound(rows, "imported_at", max),
        "booked_credit_reference_coverage_ratio": (
            sum(1 for row in booked_credits if str(row["reference"] or "").strip()) / len(booked_credits)
            if booked_credits else None
        ),
    },
    "source_control_plane": source_control,
    "safety": {
        "database_read_only": True,
        "mutations": False,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
        "row_level_identifiers_emitted": False,
        "reference_values_emitted": False,
    },
}

forbidden_keys = (
    "transaction_id", "account_id", "counterparty", "reference", "raw", "cursor", "last_error",
    "password", "secret", "token", "credential", "api_key", "authorization", "email", "phone", "iban",
)
forbidden_value = re.compile(r"(?i)(bearer |basic |-----begin|gh[pousr]_|sk_live_|AKIA[0-9A-Z]{16})")

def scan(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in forbidden_keys:
                raise SystemExit("QONTO_LEDGER_DIAGNOSIS_SENSITIVE_KEY")
            scan(child)
    elif isinstance(value, list):
        for child in value:
            scan(child)
    elif isinstance(value, str) and forbidden_value.search(value):
        raise SystemExit("QONTO_LEDGER_DIAGNOSIS_SENSITIVE_VALUE")

scan(report)
print(json.dumps(report, indent=2, sort_keys=True))
PY
