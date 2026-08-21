#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"

expected_sha="${EXPECTED_DEPLOYED_SHA:-}"
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || { echo FINANCE_RECONCILIATION_EXPECTED_SHA_INVALID >&2; exit 64; }
deployed_sha="$(git -C "$repo" rev-parse HEAD)"
[[ "$deployed_sha" == "$expected_sha" ]] || { echo FINANCE_RECONCILIATION_DEPLOYED_SHA_MISMATCH >&2; exit 65; }

source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"
compose_subcommand=compose

# Read only the already-persisted production ledgers. No provider call and no
# provider credential is needed for this proof.
docker "$compose_subcommand" exec -T \
  -e EXPECTED_DEPLOYED_SHA="$expected_sha" \
  drcloud-os python - <<'PY'
from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

from dr_cloud_sync.finance_reconciliation import reconcile_sumup_payouts_to_bank

expected_sha = os.environ.get("EXPECTED_DEPLOYED_SHA", "")
if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
    raise SystemExit("FINANCE_RECONCILIATION_EXPECTED_SHA_INVALID")

data_dir = Path(os.environ.get("DRCLOUD_DATA_DIR", "/data"))
database = data_dir / "drcloud.db"
result = reconcile_sumup_payouts_to_bank(database, bank_provider="Qonto")
status = result.get("status")
if status not in {"MEASURABLE", "NO_DATA", "UNMEASURABLE"}:
    raise SystemExit("FINANCE_RECONCILIATION_STATUS_INVALID")

aggregate = {
    "status": status,
    "reason": result.get("reason"),
    "payouts_total": result.get("payouts_total"),
    "matched": result.get("matched"),
    "unresolved": result.get("unresolved"),
    "ambiguous": result.get("ambiguous"),
    "coverage_ratio": result.get("coverage_ratio"),
}

if status == "MEASURABLE":
    values = [aggregate[key] for key in ("payouts_total", "matched", "unresolved", "ambiguous")]
    if not all(isinstance(value, int) and value >= 0 for value in values):
        raise SystemExit("FINANCE_RECONCILIATION_COUNTS_INVALID")
    if aggregate["matched"] + aggregate["unresolved"] + aggregate["ambiguous"] != aggregate["payouts_total"]:
        raise SystemExit("FINANCE_RECONCILIATION_COUNTS_INCONSISTENT")
    expected_ratio = aggregate["matched"] / aggregate["payouts_total"] if aggregate["payouts_total"] else None
    if aggregate["coverage_ratio"] != expected_ratio:
        raise SystemExit("FINANCE_RECONCILIATION_RATIO_INCONSISTENT")
elif status == "NO_DATA":
    if aggregate["payouts_total"] != 0 or aggregate["coverage_ratio"] is not None:
        raise SystemExit("FINANCE_RECONCILIATION_NO_DATA_INVALID")
else:
    if any(aggregate[key] is not None for key in ("payouts_total", "matched", "unresolved", "ambiguous", "coverage_ratio")):
        raise SystemExit("FINANCE_RECONCILIATION_UNMEASURABLE_INVALID")

report = {
    "schema_version": 1,
    "environment": "production",
    "result": "PRODUCTION_FINANCE_RECONCILIATION_CAPTURED",
    "evidence_level": "PRODUCTION_READ_ONLY_LOCAL_LEDGER_FACTS",
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "deployed_sha": expected_sha,
    "reconciliation": aggregate,
    "authority": {
        "payouts": "LOCAL_LEDGER_SUMUP_PAYOUTS",
        "bank_credits": "LOCAL_LEDGER_QONTO_BOOKED_CREDITS",
        "provider_authority_totals_proven": False,
        "end_to_end_funnel_proven": False,
    },
    "matching_contract": {
        "reference_match": "EXACT_NORMALIZED",
        "amount_match": "EXACT_DECIMAL",
        "currency_match": "EXACT",
        "fuzzy_fallback": False,
        "bank_credit_single_use": True,
    },
    "safety": {
        "database_read_only": True,
        "mutations": False,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
        "row_level_identifiers_emitted": False,
    },
}

forbidden_keys = (
    "payout_id", "bank_transaction_id", "reference", "raw", "password",
    "secret", "token", "credential", "api_key", "authorization", "email", "phone",
)
forbidden_value = re.compile(r"(?i)(bearer |basic |-----begin|gh[pousr]_|sk_live_|AKIA[0-9A-Z]{16})")

def scan(value):
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker == lowered for marker in forbidden_keys):
                raise SystemExit("FINANCE_RECONCILIATION_EVIDENCE_SENSITIVE_KEY")
            scan(child)
    elif isinstance(value, list):
        for child in value:
            scan(child)
    elif isinstance(value, str) and forbidden_value.search(value):
        raise SystemExit("FINANCE_RECONCILIATION_EVIDENCE_SENSITIVE_VALUE")

scan(report)
print(json.dumps(report, indent=2, sort_keys=True))
PY
