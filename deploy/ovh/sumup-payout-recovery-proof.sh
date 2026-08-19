#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"

# Narrow production repair/proof. It only calls the documented read-only SumUp
# payouts GET when the control plane still says ERROR. Successful imports are
# normal idempotent local ledger writes through DataHub; no provider mutation is
# possible from this code path.
docker compose exec -T drcloud-os python - <<'PY'
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys

from dr_cloud_sync.data_hub import DataHub
from dr_cloud_sync.os_config import OSSettings
from dr_cloud_sync.qonto import EnvironmentSecretProvider
from dr_cloud_sync.sumup import PaymentSettlementLedger, SumUpProvider

SOURCE_ID = "sumup_payouts"
JOB_ID = "sync_sumup_payouts"

settings = OSSettings.from_env(require_secrets=False)
database = settings.database


def facts():
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        source = db.execute(
            "SELECT source_id,status,last_attempt_at,last_success_at,records_available,last_run_id "
            "FROM data_sources WHERE source_id=?",
            (SOURCE_ID,),
        ).fetchone()
        count = int(db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0])
    if source is None:
        raise SystemExit("SUMUP_PAYOUT_RECOVERY_SOURCE_MISSING")
    return dict(source), count


before, before_count = facts()
action = "NOT_NEEDED"
failure = None
job_status = None
exit_code = 0

if before["status"] == "ERROR":
    action = "RETRY_EXECUTED"
    secrets = EnvironmentSecretProvider(os.environ, {"sumup.production": "SUMUP_API_KEY"})
    secret_ref = os.environ.get("SUMUP_SECRET_REF") or ("sumup.production" if os.environ.get("SUMUP_API_KEY") else "")
    provider = SumUpProvider(
        os.environ.get("SUMUP_MERCHANT_CODE"),
        secret_ref,
        secrets,
        base_url=os.environ.get("SUMUP_API_URL", "https://api.sumup.com"),
        timeout=float(os.environ.get("SUMUP_TIMEOUT_SECONDS", "8")),
    )
    if not provider.configured:
        failure = {"category": "CONFIGURATION", "stage": "configuration", "http_status": None, "exception_type": "ConfigurationError"}
        exit_code = 1
    else:
        hub = DataHub(database)
        ledger = PaymentSettlementLedger(database)

        def operation(cursor):
            result = ledger.sync(provider, cursor)
            result["settlements"] = ledger.reconcile()
            return result

        try:
            job = hub.run(JOB_ID, operation, manual=True)
            job_status = job.get("status") if job else None
        except Exception as exc:
            diagnostic = getattr(exc, "diagnostic", {}) or {}
            failure = {
                "category": diagnostic.get("category") or "UNKNOWN",
                "stage": diagnostic.get("stage") or "execution",
                "http_status": diagnostic.get("http_status"),
                "exception_type": type(exc).__name__,
            }
            exit_code = 1

after, after_count = facts()
result = "SUMUP_PAYOUT_RECOVERY_PROVEN" if (
    exit_code == 0
    and after["status"] == "CONNECTED"
    and after_count >= before_count
    and (action == "NOT_NEEDED" or after.get("last_success_at"))
) else "SUMUP_PAYOUT_RECOVERY_FAILED"
if result != "SUMUP_PAYOUT_RECOVERY_PROVEN":
    exit_code = 1

report = {
    "schema_version": 1,
    "environment": "production",
    "result": result,
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "action": action,
    "source": {
        "source_id": SOURCE_ID,
        "before_status": before["status"],
        "after_status": after["status"],
        "before_last_success_at": before["last_success_at"],
        "after_last_success_at": after["last_success_at"],
        "before_records_available": before["records_available"],
        "after_records_available": after["records_available"],
        "before_last_run_id": before["last_run_id"],
        "after_last_run_id": after["last_run_id"],
    },
    "ledger": {
        "before_records": before_count,
        "after_records": after_count,
        "nondecreasing": after_count >= before_count,
    },
    "job": {"job_id": JOB_ID, "status": job_status},
    "failure": failure,
    "safety": {
        "provider_method": "GET_ONLY",
        "provider_mutations": False,
        "local_mutations": action == "RETRY_EXECUTED",
        "idempotent_ledger_upsert": True,
        "raw_provider_payload_in_evidence": False,
        "credentials_in_evidence": False,
    },
}
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(exit_code)
PY
