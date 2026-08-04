#!/usr/bin/env bash
set -euo pipefail
env_file=${DRCLOUD_ENV_FILE:-/opt/drcloud-os/deploy/ovh/drcloud.env}
IFS= read -r qonto_credential
IFS= read -r shopcaisse_inbox
test -f "$env_file"
[[ -n "$qonto_credential" ]] || { echo "QONTO_CREDENTIAL est absent" >&2; exit 1; }
umask 077
python3 - "$env_file" "$qonto_credential" "$shopcaisse_inbox" <<'PY'
import os,pathlib,sys,tempfile
path=pathlib.Path(sys.argv[1]); values={"QONTO_CREDENTIAL_REF":"env:QONTO_CREDENTIAL","QONTO_CREDENTIAL":sys.argv[2],"QONTO_API_URL":"https://thirdparty.qonto.com","SHOPCAISSE_SALES_INBOX":sys.argv[3]}
lines=path.read_text().splitlines(); seen=set(); output=[]
for line in lines:
    key=line.split("=",1)[0] if "=" in line else ""
    if key in values: output.append(f"{key}={values[key]}");seen.add(key)
    else: output.append(line)
output.extend(f"{key}={value}" for key,value in values.items() if key not in seen)
fd,temporary=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent,text=True)
try:
    with os.fdopen(fd,"w") as stream:
        stream.write("\n".join(output)+"\n");stream.flush();os.fsync(stream.fileno())
    os.chmod(temporary,0o600);os.replace(temporary,path)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY
[[ "$(stat -c '%a' "$env_file")" == 600 ]] || { echo "QONTO_CREDENTIAL n'a pas été injecté dans le runtime" >&2; exit 1; }
