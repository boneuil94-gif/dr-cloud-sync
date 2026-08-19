#!/usr/bin/env bash
set -Eeuo pipefail

PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || { echo OFFSITE_RECOVERY_PYTHON_MISSING >&2; exit 127; }
script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"; [[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$repo/deploy/ovh/deployment-environment.sh"
cd "$repo/deploy/ovh"
started_epoch="$(date +%s)"; started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
exec 8>"${DRCLOUD_OFFSITE_RECOVERY_LOCK:-/tmp/drcloud-os-offsite-recovery.lock}"
flock -n 8 || { echo OFFSITE_RECOVERY_ALREADY_RUNNING >&2; exit 1; }

required=(DRCLOUD_RESTIC_IMAGE OFFSITE_RESTIC_REPOSITORY OFFSITE_RESTIC_PASSWORD OFFSITE_S3_ACCESS_KEY_ID OFFSITE_S3_SECRET_ACCESS_KEY OFFSITE_S3_REGION)
for name in "${required[@]}"; do [[ -n "${!name:-}" ]] || { echo OFFSITE_NOT_CONFIGURED >&2; exit 1; }; done
[[ "$DRCLOUD_RESTIC_IMAGE" =~ ^[^[:space:]]+@sha256:[a-fA-F0-9]{64}$ ]] || { echo RESTIC_IMAGE_NOT_IMMUTABLE >&2; exit 1; }

work="$(mktemp -d -t drcloud-offsite-recovery.XXXXXX)"
restic_uid="$(id -u)"; restic_gid="$(id -g)"
[[ "$restic_uid" =~ ^[0-9]+$ && "$restic_gid" =~ ^[0-9]+$ ]] && (( restic_uid > 0 )) \
  || { echo OFFSITE_RESTIC_IDENTITY_INVALID >&2; exit 1; }
restore="$work/remote-restore"; mkdir -m 700 "$restore"
# Restic uses the unprivileged staging owner's identity, leaving the writable
# restore destination inaccessible to the owner's group and other host users.
chmod 700 "$restore" || { echo OFFSITE_RESTORE_STAGING_INVALID >&2; exit 1; }
[[ -z "$(find "$restore" -mindepth 1 -print -quit)" ]] || { echo OFFSITE_RESTORE_DESTINATION_NOT_EMPTY >&2; exit 1; }
container="drcloud-offsite-recovery-${$}"; network="drcloud-offsite-recovery-${$}"; volume="drcloud-offsite-recovery-data-${$}"
cleanup() {
  local rc=$?
  docker rm -f "$container" >/dev/null 2>&1 || true; docker network rm "$network" >/dev/null 2>&1 || true
  [[ "$volume" =~ ^drcloud-offsite-recovery-data-[0-9]+$ ]] && docker volume rm -f "$volume" >/dev/null 2>&1 || true
  rm -rf -- "$work"; exit "$rc"
}
trap cleanup EXIT INT TERM

# Production is consulted read-only for measurement, never as restore input.
live_watermark="$work/live-watermark.json"
docker compose exec -T drcloud-os dr-cloud-sync recovery-watermark --json >"$live_watermark" 2>/dev/null || printf '{}\n' >"$live_watermark"

export RESTIC_REPOSITORY="$OFFSITE_RESTIC_REPOSITORY" RESTIC_PASSWORD="$OFFSITE_RESTIC_PASSWORD"
export AWS_ACCESS_KEY_ID="$OFFSITE_S3_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$OFFSITE_S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="$OFFSITE_S3_REGION" AWS_REGION="$OFFSITE_S3_REGION"
[[ -z "${OFFSITE_S3_ENDPOINT:-}" ]] || export AWS_ENDPOINT_URL="$OFFSITE_S3_ENDPOINT"
restic() {
  docker run --rm --network bridge --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --user "$restic_uid:$restic_gid" --cap-drop ALL --security-opt no-new-privileges \
    --mount "type=bind,src=$restore,dst=/restore" \
    -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    -e AWS_DEFAULT_REGION -e AWS_REGION -e RESTIC_CACHE_DIR=/tmp/restic-cache \
    ${OFFSITE_S3_ENDPOINT:+-e AWS_ENDPOINT_URL} "$DRCLOUD_RESTIC_IMAGE" "$@"
}
restic snapshots --json --tag drcloud-os >"$work/snapshots.json" || { echo OFFSITE_REMOTE_CHECK_FAILED >&2; exit 1; }
snapshot="$($PYTHON_BIN - "$work/snapshots.json" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1])); rows.sort(key=lambda x:x.get("time",""),reverse=True)
if not rows: raise SystemExit(1)
print(rows[0]["id"])
PY
)" || { echo OFFSITE_SNAPSHOT_ABSENT >&2; exit 1; }
[[ "$snapshot" =~ ^[a-f0-9]{8,64}$ ]] || { echo OFFSITE_SNAPSHOT_ABSENT >&2; exit 1; }
restic check --read-data-subset="${OFFSITE_RESTIC_CHECK_SUBSET:-1/100}" >/dev/null || { echo OFFSITE_REMOTE_CHECK_FAILED >&2; exit 1; }
restic restore "$snapshot" --target /restore >/dev/null || { echo OFFSITE_RESTORE_FAILED >&2; exit 1; }

# Restic backed up /source.  No production volume or /data/backups path is read.
bundle="$restore/source"
[[ -d "$bundle" ]] || { echo OFFSITE_RESTORE_BUNDLE_INVALID >&2; exit 1; }
facts="$work/facts.json"
"$PYTHON_BIN" - "$bundle" "$facts" <<'PY' || { echo OFFSITE_RESTORE_RUNTIME_INVALID >&2; exit 1; }
import hashlib,json,os,sqlite3,sys
root,out=sys.argv[1:]; manifest=json.load(open(os.path.join(root,"metadata.json")))
required=["drcloud.db","catalogue.json","catalogue-report.json"]
if manifest.get("required_runtime_files")!=required: raise SystemExit(1)
entries={x.get("path"):x for x in manifest.get("files",[]) if isinstance(x,dict)}
for name in required:
 p=os.path.join(root,name); raw=open(p,"rb").read(); e=entries.get(name)
 if not e or len(raw)!=e.get("size") or hashlib.sha256(raw).hexdigest()!=e.get("sha256"): raise SystemExit(1)
 if name.endswith(".json"): json.load(open(p))
dbp=os.path.join(root,"drcloud.db")
with sqlite3.connect(f"file:{dbp}?mode=ro",uri=True) as db:
 quick=db.execute("pragma quick_check").fetchone()[0]; integrity=db.execute("pragma integrity_check").fetchone()[0]; foreign=list(db.execute("pragma foreign_key_check"))
if quick!="ok" or integrity!="ok" or foreign: raise SystemExit(1)
json.dump({"quick_check":quick,"integrity_check":integrity,"foreign_key_check":"OK","created_at":manifest.get("created_at"),"data_max_at":manifest.get("data_max_at"),"recovery_watermark":manifest.get("recovery_watermark")},open(out,"w"))
PY

image="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q drcloud-os)")"
[[ -n "$image" ]] || { echo OFFSITE_APP_IMAGE_MISSING >&2; exit 1; }
docker volume create "$volume" >/dev/null
docker run --rm --user 0:0 --network none --read-only \
  --mount "type=volume,src=$volume,dst=/data" --mount "type=bind,src=$bundle,dst=/seed,readonly" \
  --entrypoint /bin/sh "$image" -c 'cp /seed/drcloud.db /seed/catalogue.json /seed/catalogue-report.json /data/; if [ -d /seed/media ]; then cp -R /seed/media /data/media; fi; chown -R drcloud:drcloud /data; chmod 700 /data; chmod 600 /data/drcloud.db /data/catalogue.json /data/catalogue-report.json' \
  || { echo OFFSITE_RESTORE_SEED_FAILED >&2; exit 1; }
docker network create --internal "$network" >/dev/null
docker run -d --name "$container" --network "$network" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges --mount "type=volume,src=$volume,dst=/data" \
  -e DRCLOUD_ENV=recovery-gameday -e DRCLOUD_SAFE_MODE=true -e BARCODE_SYNC_MODE=dry-run \
  -e DRCLOUD_DATA_DIR=/data -e DRCLOUD_HOST=0.0.0.0 -e DRCLOUD_PORT=8080 \
  -e DRCLOUD_SECRET_KEY=recovery-isolated-nonproduction -e DRCLOUD_ADMIN_USERNAME=recovery \
  -e DRCLOUD_ADMIN_PASSWORD=recovery-isolated-unused "$image" >/dev/null || { echo OFFSITE_APP_BOOT_FAILED >&2; exit 1; }
[[ "$(docker inspect --format '{{.State.Running}}' "$container")" == true ]] || { echo OFFSITE_APP_BOOT_FAILED >&2; exit 1; }
health=; for _ in $(seq 1 45); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
  [[ "$state" == healthy ]] && { health=ok; break; }
  [[ "$state" == unhealthy ]] && break; sleep 1
done
[[ "$health" == ok ]] || { echo OFFSITE_HEALTH_FAILED >&2; exit 1; }
docker exec -i "$container" python - <<'PY' >/dev/null || { echo OFFSITE_BUSINESS_PROBE_FAILED >&2; exit 1; }
import urllib.error,urllib.request
try: urllib.request.urlopen("http://127.0.0.1:8080/api/roadmap",timeout=3).read()
except urllib.error.HTTPError as e:
    if e.code not in (401,403): raise
PY

report="$DRCLOUD_DEPLOYMENT_STATE_DIR/offsite_recovery_evidence_production.json"
ended_epoch="$(date +%s)"; completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - "$facts" "$report" "$snapshot" "$started_at" "$completed_at" "$((ended_epoch-started_epoch))" "$live_watermark" <<'PY'
import datetime,json,os,re,sys
from dr_cloud_sync.recovery_watermark import compare_recovery_watermarks
facts=json.load(open(sys.argv[1])); out=sys.argv[2]
data_max=facts.pop("data_max_at",None); created=facts.pop("created_at",None); backup_watermark=facts.pop("recovery_watermark",None)
try: live=json.load(open(sys.argv[7])); comparison=compare_recovery_watermarks(live,backup_watermark)
except Exception: live={}; comparison={"confidence":"UNKNOWN","observed_rpo_seconds":None,"comparable_sources":0,"unmeasurable_sources":0,"business_data_gap_seconds":None,"sync_progress_gap_seconds":None}
rpo=comparison.get("observed_rpo_seconds")
if not backup_watermark and created:
 try: rpo=max(0,(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(created.replace("Z","+00:00"))).total_seconds())
 except Exception: pass
d={"schema_version":1,"environment":"production","evidence_level":"OFFSITE_ENCRYPTED_BACKUP_RESTORE",
 "backup":{"source_result":"PRODUCTION_BACKUP_VALID","offsite_upload":"OFFSITE_UPLOAD_PROVEN","encryption":"RESTIC_CLIENT_SIDE_ENCRYPTED","location_classification":"OFF_HOST_OBJECT_STORAGE"},
 "remote":{"snapshot_available":True,"integrity":"PASS","snapshot":sys.argv[3]},
 "restore":{"result":"OFFSITE_RESTORE_PROVEN","app_boot":"APP_BOOT_OK","health":"HEALTH_OK",**facts},
 "timing":{"started_at":sys.argv[4],"completed_at":sys.argv[5],"observed_restore_seconds":int(sys.argv[6]),"live_watermark_available":bool(live.get("schema_version")),"backup_watermark_available":bool(backup_watermark),"comparable_sources":comparison.get("comparable_sources",0),"unmeasurable_sources":comparison.get("unmeasurable_sources",0),"business_data_gap_seconds":comparison.get("business_data_gap_seconds"),"sync_progress_gap_seconds":comparison.get("sync_progress_gap_seconds"),"observed_rpo_seconds":round(rpo,3) if rpo is not None else None,"rpo_method":"live_vs_backup_source_watermarks" if backup_watermark else "backup_created_at_proxy","rpo_confidence":comparison.get("confidence") if backup_watermark else "LOW"},
 "safety":{"safe_mode":True,"external_provider_auth":"NONE","production_volume_mounted":False,"production_port_published":False,"local_backup_used_for_restore":False,"live_watermark_used_for_measurement_only":True,"cloud_material_persisted":False,"network":"INTERNAL_ONLY"}}
forbidden=("password","secret","token","credential","api_key","private_key","authorization")
def scan(v):
 if isinstance(v,dict):
  for k,x in v.items():
   if any(w in k.lower() for w in forbidden): raise SystemExit("forbidden evidence key")
   scan(x)
 elif isinstance(v,list):
  for x in v: scan(x)
 elif isinstance(v,str) and re.search(r"(?i)(bearer |basic |AKIA[0-9A-Z]{16}|-----begin|gh[pousr]_|sk_live_)",v): raise SystemExit("forbidden evidence value")
scan(d); os.makedirs(os.path.dirname(out),exist_ok=True); tmp=out+".tmp"
open(tmp,"w").write(json.dumps(d,indent=2,sort_keys=True)+"\n"); os.chmod(tmp,0o600); os.replace(tmp,out)
PY

# Update only non-sensitive observability state after the complete proof.
status="${DRCLOUD_OFFSITE_STATUS_FILE:-$DRCLOUD_DEPLOYMENT_STATE_DIR/offsite_backup_status.json}"
"$PYTHON_BIN" - "$status" "$snapshot" "$completed_at" <<'PY'
import json,os,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
d.update({"restore_proof":"OFFSITE_RESTORE_PROVEN","restore_proof_at":sys.argv[3],"last_snapshot":sys.argv[2]})
tmp=sys.argv[1]+".tmp"; open(tmp,"w").write(json.dumps(d,indent=2,sort_keys=True)+"\n"); os.chmod(tmp,0o600); os.replace(tmp,sys.argv[1])
PY
echo OFFSITE_RESTORE_PROVEN
echo APP_BOOT_OK
echo HEALTH_OK
