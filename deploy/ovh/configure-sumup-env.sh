#!/usr/bin/env bash
set -euo pipefail
env_file="${DRCLOUD_ENV_FILE:-/opt/drcloud-os/deploy/ovh/drcloud.env}"
IFS= read -r api_key
IFS= read -r merchant_code
test -n "$api_key" && test -n "$merchant_code"
umask 077; touch "$env_file"
python3 - "$env_file" "$api_key" "$merchant_code" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);values={"SUMUP_API_KEY":sys.argv[2],"SUMUP_MERCHANT_CODE":sys.argv[3],"SUMUP_API_URL":"https://api.sumup.com","SUMUP_SYNC_INTERVAL_SECONDS":"900"}
lines=p.read_text().splitlines();kept=[line for line in lines if line.split("=",1)[0] not in values]
p.write_text("\n".join(kept+[f"{k}={v}" for k,v in values.items()])+"\n")
PY
chmod 600 "$env_file"
