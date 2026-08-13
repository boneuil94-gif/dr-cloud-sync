#!/usr/bin/env bash
set -Eeuo pipefail

# Restic is deliberately run outside drcloud-os.  Its environment is supplied
# by the caller and is never written by this script.
PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || { echo OFFSITE_PYTHON_RUNTIME_MISSING >&2; exit 127; }
script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"; [[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"

lock_file="${DRCLOUD_OFFSITE_LOCK:-/tmp/drcloud-os-offsite-backup.lock}"
exec 8>"$lock_file"
flock -n 8 || { echo OFFSITE_BACKUP_ALREADY_RUNNING >&2; exit 1; }
work="$(mktemp -d -t drcloud-offsite.XXXXXX)"
cleanup() { local rc=$?; rm -rf -- "$work"; exit "$rc"; }
trap cleanup EXIT INT TERM

status_file="${DRCLOUD_OFFSITE_STATUS_FILE:-$DRCLOUD_DEPLOYMENT_STATE_DIR/offsite_backup_status.json}"
attempt="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_status() {
  local result="$1" snapshot="${2:-}" remote="${3:-UNKNOWN}" retention="${4:-RETENTION_NOT_CONFIGURED}"
  "$PYTHON_BIN" - "$status_file" "$attempt" "$result" "$snapshot" "$remote" "$retention" <<'PY'
import json,os,sys
p,attempt,result,snapshot,remote,retention=sys.argv[1:]
try: old=json.load(open(p))
except Exception: old={}
success=attempt if result=="OFFSITE_REMOTE_CHECK_PROVEN" else old.get("last_success_at")
d={"configured":result not in ("OFFSITE_NOT_CONFIGURED","RESTIC_IMAGE_NOT_IMMUTABLE"),
 "last_attempt_at":attempt,"last_success_at":success,"last_snapshot":snapshot or old.get("last_snapshot"),
 "last_result":result,"location_classification":"OFF_HOST_OBJECT_STORAGE",
 "encryption":"RESTIC_CLIENT_SIDE_ENCRYPTED","remote_check":remote,
 "restore_proof":old.get("restore_proof","UNKNOWN"),"restore_proof_at":old.get("restore_proof_at"),
 "retention":retention}
os.makedirs(os.path.dirname(p),exist_ok=True); tmp=p+".tmp"
open(tmp,"w").write(json.dumps(d,indent=2,sort_keys=True)+"\n"); os.chmod(tmp,0o600); os.replace(tmp,p)
PY
}
fail() { write_status "$1" "${2:-}" "${3:-UNKNOWN}"; echo "$1" >&2; exit 1; }

required=(DRCLOUD_RESTIC_IMAGE OFFSITE_RESTIC_REPOSITORY OFFSITE_RESTIC_PASSWORD OFFSITE_S3_ACCESS_KEY_ID OFFSITE_S3_SECRET_ACCESS_KEY OFFSITE_S3_REGION)
for name in "${required[@]}"; do [[ -n "${!name:-}" ]] || fail OFFSITE_NOT_CONFIGURED; done
[[ "$DRCLOUD_RESTIC_IMAGE" == *@sha256:* ]] || fail RESTIC_IMAGE_NOT_IMMUTABLE
[[ "$DRCLOUD_RESTIC_IMAGE" =~ ^[^[:space:]]+@sha256:[a-fA-F0-9]{64}$ ]] || fail RESTIC_IMAGE_NOT_IMMUTABLE

restic() {
  docker run --rm --network bridge --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL --security-opt no-new-privileges \
    --mount "type=bind,src=$work/source,dst=/source,readonly" \
    -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    -e AWS_DEFAULT_REGION -e AWS_REGION -e RESTIC_CACHE_DIR=/tmp/restic-cache \
    ${OFFSITE_S3_ENDPOINT:+-e AWS_ENDPOINT_URL} \
    "$DRCLOUD_RESTIC_IMAGE" "$@"
}
export RESTIC_REPOSITORY="$OFFSITE_RESTIC_REPOSITORY" RESTIC_PASSWORD="$OFFSITE_RESTIC_PASSWORD"
export AWS_ACCESS_KEY_ID="$OFFSITE_S3_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$OFFSITE_S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="$OFFSITE_S3_REGION" AWS_REGION="$OFFSITE_S3_REGION"
[[ -z "${OFFSITE_S3_ENDPOINT:-}" ]] || export AWS_ENDPOINT_URL="$OFFSITE_S3_ENDPOINT"

docker compose exec -T drcloud-os dr-cloud-sync backup-status --json >"$work/inventory.json" || fail OFFSITE_BACKUP_SOURCE_INVALID
if ! "$PYTHON_BIN" - "$work/inventory.json" >"$work/selection" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); rows=[x for x in d.get("backups",[]) if x.get("status")=="VALID" and x.get("backup_class")=="APP_RESTORABLE" and x.get("runtime_files_complete") is True]
if not rows: raise SystemExit(1)
r=rows[0]; bid=r.get("backup_id","")
if not bid.startswith("drcloud-os-backup-") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in bid): raise SystemExit(1)
print(bid)
PY
then
  "$repo/deploy/ovh/backup.sh" >/dev/null || fail OFFSITE_BACKUP_SOURCE_INVALID
  docker compose exec -T drcloud-os dr-cloud-sync backup-status --json >"$work/inventory.json" || fail OFFSITE_BACKUP_SOURCE_INVALID
  "$PYTHON_BIN" - "$work/inventory.json" >"$work/selection" <<'PY' || fail OFFSITE_BACKUP_SOURCE_INVALID
import json,sys
rows=[x for x in json.load(open(sys.argv[1])).get("backups",[]) if x.get("status")=="VALID" and x.get("backup_class")=="APP_RESTORABLE" and x.get("runtime_files_complete") is True]
if not rows: raise SystemExit(1)
bid=rows[0].get("backup_id","");
if not bid.startswith("drcloud-os-backup-") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in bid): raise SystemExit(1)
print(bid)
PY
fi
backup_id="$(cat "$work/selection")"
mkdir -m 700 "$work/source"
docker compose cp -L "drcloud-os:/data/backups/$backup_id/." "$work/source/" >/dev/null || fail OFFSITE_BACKUP_SOURCE_INVALID

# Independently re-validate the copied manifest and every declared file before upload.
"$PYTHON_BIN" - "$work/source" <<'PY' || fail OFFSITE_BACKUP_SOURCE_INVALID
import hashlib,json,os,sys
root=sys.argv[1]; m=json.load(open(os.path.join(root,"metadata.json")))
required=["drcloud.db","catalogue.json","catalogue-report.json"]
if m.get("required_runtime_files")!=required: raise SystemExit(1)
entries={x.get("path"):x for x in m.get("files",[]) if isinstance(x,dict)}
for name in required:
 p=os.path.join(root,name); e=entries.get(name)
 if not e or not os.path.isfile(p): raise SystemExit(1)
 raw=open(p,"rb").read()
 if len(raw)!=e.get("size") or hashlib.sha256(raw).hexdigest()!=e.get("sha256"): raise SystemExit(1)
 json.load(open(p)) if name.endswith(".json") else None
PY

if ! restic snapshots --json >/dev/null 2>&1; then
  # init is safe only for a genuinely absent repository; other errors remain failures.
  restic cat config >/dev/null 2>&1 || restic init >/dev/null || fail OFFSITE_UPLOAD_FAILED
fi
restic backup /source \
  --tag drcloud-os --tag "$backup_id" --json >"$work/upload.json" || fail OFFSITE_UPLOAD_FAILED
snapshot="$($PYTHON_BIN - "$work/upload.json" <<'PY'
import json,sys
sid=""
for line in open(sys.argv[1]):
 try:
  d=json.loads(line); sid=d.get("snapshot_id",sid)
 except json.JSONDecodeError: pass
print(sid)
PY
)"
[[ "$snapshot" =~ ^[a-f0-9]{8,64}$ ]] || fail OFFSITE_UPLOAD_FAILED
write_status OFFSITE_UPLOAD_PROVEN "$snapshot" UNKNOWN
restic snapshots --json --tag "$backup_id" >"$work/snapshots.json" || fail OFFSITE_REMOTE_CHECK_FAILED "$snapshot" FAILED
"$PYTHON_BIN" - "$work/snapshots.json" "$snapshot" <<'PY' || fail OFFSITE_REMOTE_CHECK_FAILED "$snapshot" FAILED
import json,sys
rows=json.load(open(sys.argv[1])); target=sys.argv[2]
raise SystemExit(not any(x.get("id","").startswith(target) or target.startswith(x.get("id","")) for x in rows))
PY
restic check --read-data-subset="${OFFSITE_RESTIC_CHECK_SUBSET:-1/100}" >/dev/null || fail OFFSITE_REMOTE_CHECK_FAILED "$snapshot" FAILED

retention=RETENTION_NOT_CONFIGURED
if [[ -n "${OFFSITE_RESTIC_KEEP_DAILY:-}" || -n "${OFFSITE_RESTIC_KEEP_WEEKLY:-}" || -n "${OFFSITE_RESTIC_KEEP_MONTHLY:-}" ]]; then
  for value in "${OFFSITE_RESTIC_KEEP_DAILY:-0}" "${OFFSITE_RESTIC_KEEP_WEEKLY:-0}" "${OFFSITE_RESTIC_KEEP_MONTHLY:-0}"; do
    [[ "$value" =~ ^[0-9]+$ ]] && (( value <= 10000 )) || fail OFFSITE_RETENTION_INVALID "$snapshot" PROVEN
  done
  args=(); [[ -z "${OFFSITE_RESTIC_KEEP_DAILY:-}" ]] || args+=(--keep-daily "$OFFSITE_RESTIC_KEEP_DAILY")
  [[ -z "${OFFSITE_RESTIC_KEEP_WEEKLY:-}" ]] || args+=(--keep-weekly "$OFFSITE_RESTIC_KEEP_WEEKLY")
  [[ -z "${OFFSITE_RESTIC_KEEP_MONTHLY:-}" ]] || args+=(--keep-monthly "$OFFSITE_RESTIC_KEEP_MONTHLY")
  restic forget --tag drcloud-os "${args[@]}" --prune >/dev/null || fail OFFSITE_RETENTION_FAILED "$snapshot" PROVEN
  retention=RETENTION_APPLIED
fi
write_status OFFSITE_REMOTE_CHECK_PROVEN "$snapshot" PROVEN "$retention"
echo OFFSITE_UPLOAD_PROVEN
echo OFFSITE_REMOTE_CHECK_PROVEN
