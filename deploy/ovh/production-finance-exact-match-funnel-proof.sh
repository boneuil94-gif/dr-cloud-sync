#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
expected_sha="${EXPECTED_DEPLOYED_SHA:-}"
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || { echo FINANCE_FUNNEL_EXPECTED_SHA_INVALID >&2; exit 64; }
deployed_sha="$(git -C "$repo" rev-parse HEAD)"
[[ "$deployed_sha" == "$expected_sha" ]] || { echo FINANCE_FUNNEL_DEPLOYED_SHA_MISMATCH >&2; exit 65; }

source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"
docker compose exec -T -e EXPECTED_DEPLOYED_SHA="$expected_sha" drcloud-os python - <<'PY'
from __future__ import annotations
import datetime,json,os,re
from pathlib import Path
from dr_cloud_sync.finance_match_funnel import exact_match_funnel

sha=os.environ.get('EXPECTED_DEPLOYED_SHA','')
if not re.fullmatch(r'[0-9a-f]{40}',sha): raise SystemExit('FINANCE_FUNNEL_EXPECTED_SHA_INVALID')
path=Path(os.environ.get('DRCLOUD_DATA_DIR','/data'))/'drcloud.db'
evidence=exact_match_funnel(path,bank_provider='qonto')
report={
 'schema_version':1,
 'environment':'production',
 'result':'PRODUCTION_FINANCE_EXACT_MATCH_FUNNEL_CAPTURED',
 'evidence_level':'PRODUCTION_READ_ONLY_LOCAL_LEDGER',
 'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),
 'deployed_sha':sha,
 'exact_match_funnel':evidence,
 'safety':{'database_read_only':True,'provider_network_calls':False,'external_provider_auth':'NONE','mutations':False,'row_level_identifiers_emitted':False,'reference_values_emitted':False,'free_form_banking_data_emitted':False},
}
forbidden={'message','response_excerpt','request_id','cursor','account_id','transaction_id','payout_id','iban','reference','label','counterparty','secret','token','credential','api_key','authorization','email','phone'}
def scan(v):
 if isinstance(v,dict):
  for k,c in v.items():
   if str(k).lower() in forbidden: raise SystemExit('FINANCE_FUNNEL_SENSITIVE_KEY')
   scan(c)
 elif isinstance(v,list):
  for c in v: scan(c)
scan(report)
print(json.dumps(report,indent=2,sort_keys=True))
PY
