#!/usr/bin/env bash
set -euo pipefail
env_file=${DRCLOUD_ENV_FILE:-/opt/drcloud-os/deploy/ovh/drcloud.env}
IFS= read -r qonto_credential
IFS= read -r shopcaisse_inbox
test -f "$env_file"
umask 077
python3 - "$env_file" "$qonto_credential" "$shopcaisse_inbox" <<'PY'
import pathlib,sys
path=pathlib.Path(sys.argv[1]); values={"QONTO_CREDENTIAL_REF":"QONTO_CREDENTIAL","QONTO_CREDENTIAL":sys.argv[2],"SHOPCAISSE_SALES_INBOX":sys.argv[3]}
lines=path.read_text().splitlines(); seen=set(); output=[]
for line in lines:
    key=line.split("=",1)[0] if "=" in line else ""
    if key in values: output.append(f"{key}={values[key]}");seen.add(key)
    else: output.append(line)
output.extend(f"{key}={value}" for key,value in values.items() if key not in seen)
path.write_text("\n".join(output)+"\n");path.chmod(0o600)
PY
