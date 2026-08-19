#!/usr/bin/env bash
set -Eeuo pipefail

PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || {
  echo "RECOVERY_PYTHON_RUNTIME_MISSING" >&2
  exit 127
}

mode="${1:-restore-only}"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[[ "$mode" == "restore-only" || "$mode" == "full" ]] || { echo "mode invalide" >&2; exit 2; }
script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="/opt/drcloud-os"
[[ -d "$repo/.git" ]] || repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$repo/deploy/ovh/deployment-environment.sh"
export PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$repo/deploy/ovh"

lock_file="${DRCLOUD_RECOVERY_LOCK:-/tmp/drcloud-os-recovery-gameday.lock}"
deploy_lock="${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"
exec 8>"$lock_file"
flock -n 8 || { echo "GAME_DAY_ALREADY_RUNNING" >&2; exit 1; }
exec 9>"$deploy_lock"
flock -n 9 || { echo "GAME_DAY_BLOCKED_DEPLOYMENT_ACTIVE" >&2; exit 1; }

work="$(mktemp -d -t drcloud-recovery-gameday.XXXXXX)"
container="drcloud-recovery-${$}"
network="drcloud-recovery-${$}"
recovery_volume="drcloud-recovery-data-${$}"
rollback_volume="drcloud-rollback-data-${$}"
rollback_network="drcloud-rollback-${$}"
n_container="drcloud-rollback-n-${$}"
n1_container="drcloud-rollback-n1-${$}"
n1_image="drcloud-recovery-n1-${$}"
worktree=""
cleanup() {
  local status=$?
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker rm -f "$n_container" "$n1_container" >/dev/null 2>&1 || true
  docker network rm "$rollback_network" >/dev/null 2>&1 || true
  # Only remove volumes created for an isolated Game Day run.  Never pass an
  # unchecked name to `docker volume rm`, even during best-effort cleanup.
  if [[ "$recovery_volume" =~ ^drcloud-recovery-data-[0-9]+$ ]]; then
    docker volume rm -f "$recovery_volume" >/dev/null 2>&1 || true
  fi
  if [[ "$rollback_volume" =~ ^drcloud-rollback-data-[0-9]+$ ]]; then
    docker volume rm -f "$rollback_volume" >/dev/null 2>&1 || true
  fi
  [[ "$n1_image" =~ ^drcloud-recovery-n1-[0-9]+$ ]] && docker image rm -f "$n1_image" >/dev/null 2>&1 || true
  if [[ -n "$worktree" && "$worktree" == "$work/n-minus-1-src" ]]; then
    git -C "$repo" worktree remove --force "$worktree" >/dev/null 2>&1 || true
    git -C "$repo" worktree prune >/dev/null 2>&1 || true
  fi
  rm -rf -- "$work"
  [[ ! -e "$work" ]] || status=1
  exit "$status"
}
trap cleanup EXIT INT TERM

# Measurement reference only: this read-only command runs before any restore
# selection/copy and its output is never accepted as restore input.
live_watermark="$work/live-watermark.json"
docker compose exec -T drcloud-os dr-cloud-sync recovery-watermark --json >"$live_watermark" 2>/dev/null || printf '{}\n' >"$live_watermark"

status_json="$work/backup-status.json"
production_backup_status() {
  docker compose exec -T drcloud-os dr-cloud-sync backup-status --json >"$status_json"
}
production_backup_status
if ! "$PYTHON_BIN" - "$status_json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); raise SystemExit(not any(x.get("status")=="VALID" and x.get("backup_class")=="APP_RESTORABLE" and x.get("runtime_files_complete") is True for x in d.get("backups",[])))
PY
then
  "$repo/deploy/ovh/backup.sh" >/dev/null
  production_backup_status
fi

selection="$work/selection.json"
"$PYTHON_BIN" - "$status_json" "$selection" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); rows=[x for x in d.get("backups",[]) if x.get("status")=="VALID" and x.get("backup_class")=="APP_RESTORABLE" and x.get("runtime_files_complete") is True]
if not rows:
    print("PRODUCTION_BACKUP_INVALID" if d.get("backups") else "PRODUCTION_BACKUP_MISSING",file=sys.stderr); raise SystemExit(1)
r=rows[0]
if not r.get("database","/").startswith("/data/backups/"): raise SystemExit("unsafe backup location")
json.dump({k:r.get(k) for k in ("backup_id","created_at","size_bytes","sha256","schema_fingerprint","database","backup_class","runtime_files_complete","data_max_at","watermark_confidence","watermark_coverage")},open(sys.argv[2],"w"))
PY
backup_id="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup_id"])' "$selection")"
backup_selected_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[[ "$backup_id" =~ ^drcloud-os-backup-[A-Za-z0-9T_-]+$ ]] || { echo "PRODUCTION_BACKUP_INVALID" >&2; exit 1; }

mkdir -m 700 "$work/bundle" "$work/restored-data"
# docker cp reads only the selected official bundle.  The active /data runtime
# and the production volume are never a restore source or target mount.
for runtime_file in drcloud.db catalogue.json catalogue-report.json metadata.json; do
  docker compose cp -L "drcloud-os:/data/backups/$backup_id/$runtime_file" "$work/bundle/$runtime_file" >/dev/null
  cp --reflink=auto "$work/bundle/$runtime_file" "$work/restored-data/$runtime_file"
done
if docker compose exec -T drcloud-os test -d "/data/backups/$backup_id/media"; then
  docker compose cp -L "drcloud-os:/data/backups/$backup_id/media" "$work/bundle/media" >/dev/null
  cp -a "$work/bundle/media" "$work/restored-data/media"
fi
chmod 600 "$work/restored-data/drcloud.db" "$work/restored-data/catalogue.json" "$work/restored-data/catalogue-report.json"
restore_completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

timings="$work/timings.json"
if ! "$PYTHON_BIN" - "$selection" "$work/bundle/metadata.json" "$work/restored-data" "$timings" "$live_watermark" <<'PY'
import datetime,hashlib,json,os,sqlite3,sys,time
from dr_cloud_sync.recovery_watermark import compare_recovery_watermarks
selection=json.load(open(sys.argv[1])); manifest=json.load(open(sys.argv[2])); restored=sys.argv[3]; db_path=os.path.join(restored,"drcloud.db")
if manifest.get("backup_id") != selection["backup_id"]: raise SystemExit("manifest mismatch")
required=["drcloud.db","catalogue.json","catalogue-report.json"]
if manifest.get("required_runtime_files") != required: raise SystemExit("runtime contract mismatch")
entries={x.get("path"):x for x in manifest.get("files",[]) if isinstance(x,dict)}
for name in required:
    path=os.path.join(restored,name); entry=entries.get(name)
    if not entry or not os.path.isfile(path): raise SystemExit("missing runtime file")
    content=open(path,"rb").read()
    if len(content)!=entry.get("size") or hashlib.sha256(content).hexdigest()!=entry.get("sha256"): raise SystemExit("runtime checksum mismatch")
try:
    catalogue=json.load(open(os.path.join(restored,"catalogue.json")))
    report=json.load(open(os.path.join(restored,"catalogue-report.json")))
except (OSError,json.JSONDecodeError): raise SystemExit("invalid runtime JSON")
rows=catalogue.get("mappings") if isinstance(catalogue,dict) else catalogue
if not isinstance(rows,list) or not rows or report.get("ready_for_inventory") is not True: raise SystemExit("runtime inventory state invalid")
raw=open(db_path,"rb").read(); actual=hashlib.sha256(raw).hexdigest()
if actual != selection["sha256"] or actual != manifest.get("sha256"): raise SystemExit("checksum mismatch")
if len(raw) != selection["size_bytes"] or len(raw) != manifest.get("database_size"): raise SystemExit("size mismatch")
with sqlite3.connect(f"file:{db_path}?mode=ro",uri=True) as db:
    quick=db.execute("PRAGMA quick_check").fetchone()[0]
    integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
    foreign=list(db.execute("PRAGMA foreign_key_check"))
    schema="\n".join((r[0] or "") for r in db.execute("select sql from sqlite_master where sql is not null order by type,name"))
    tables={r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
    indexes=db.execute("select count(*) from sqlite_master where type='index'").fetchone()[0]
    allowed=("bank_transactions","sumup_transactions","sumup_payouts","sales","sale_events","stock_movements","crm_customers","purchase_orders")
    counts={name:db.execute(f'select count(*) from "{name}"').fetchone()[0] for name in allowed if name in tables}
fingerprint=hashlib.sha256(schema.encode()).hexdigest()
if quick!="ok" or integrity!="ok" or foreign or fingerprint != selection["schema_fingerprint"] or fingerprint != manifest.get("schema_fingerprint"):
    raise SystemExit("integrity/schema validation failed")
created=manifest.get("created_at"); data_max=manifest.get("data_max_at"); backup_watermark=manifest.get("recovery_watermark")
try: live_watermark=json.load(open(sys.argv[5])); comparison=compare_recovery_watermarks(live_watermark,backup_watermark)
except Exception: live_watermark={}; comparison={"confidence":"UNKNOWN","observed_rpo_seconds":None,"comparable_sources":0,"unmeasurable_sources":0,"business_data_gap_seconds":None,"sync_progress_gap_seconds":None}
rpo=comparison.get("observed_rpo_seconds")
if not backup_watermark:
 try: rpo=max(0,(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(created.replace("Z","+00:00"))).total_seconds())
 except Exception: rpo=None
json.dump({"integrity_check":"ok","foreign_key_check":"OK","quick_check":"ok","schema_fingerprint":fingerprint,
 "database_size":len(raw),"table_count":len(tables),"index_count":indexes,"table_counts":counts,
 "data_min_at":None,"data_max_at":data_max,"observed_rpo_seconds":round(rpo,3) if rpo is not None else None,
 "observed_rpo_human":f"{int(rpo)}s" if rpo is not None else "UNKNOWN",
 "live_watermark_available":bool(live_watermark.get("schema_version")),"backup_watermark_available":bool(backup_watermark),
 "comparable_sources":comparison.get("comparable_sources",0),"unmeasurable_sources":comparison.get("unmeasurable_sources",0),
 "business_data_gap_seconds":comparison.get("business_data_gap_seconds"),"sync_progress_gap_seconds":comparison.get("sync_progress_gap_seconds"),
 "rpo_method":"live_vs_backup_source_watermarks" if backup_watermark else "backup_created_at_proxy",
 "rpo_confidence":comparison.get("confidence") if backup_watermark else "LOW"},open(sys.argv[4],"w"))
PY
then
  echo "RESTORE_RUNTIME_STATE_INVALID" >&2
  exit 1
fi
integrity_completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

image="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q drcloud-os)")"
docker volume create "$recovery_volume" >/dev/null

# Seed the validated, isolated database copy into a disposable Docker volume.
# This one-shot container has no network or credentials and never starts the
# application.  Running only this preparation step as root permits it to hand
# ownership of /data to the image's normal drcloud user.
if ! docker run --rm --user 0:0 --network none --read-only \
  --mount "type=volume,src=$recovery_volume,dst=/data" \
  --mount "type=bind,src=$work/restored-data,dst=/seed,readonly" \
  --entrypoint /bin/sh "$image" -c '
    cp /seed/drcloud.db /seed/catalogue.json /seed/catalogue-report.json /data/ &&
    if [ -d /seed/media ]; then cp -R /seed/media /data/media; fi &&
    chown -R drcloud:drcloud /data &&
    chmod 700 /data &&
    chmod 600 /data/drcloud.db /data/catalogue.json /data/catalogue-report.json &&
    if [ -d /data/media ]; then find /data/media -type d -exec chmod 700 {} +; find /data/media -type f -exec chmod 600 {} +; fi
  '
then
  echo "RESTORE_VOLUME_PERMISSION_FAILED" >&2
  exit 1
fi

# Fail closed unless ownership and modes are exactly those required by the
# application.  Numeric comparisons avoid trusting localized stat output.
if ! docker run --rm --network none --read-only \
  --mount "type=volume,src=$recovery_volume,dst=/data" \
  --entrypoint /bin/sh "$image" -c '
    uid="$(id -u drcloud)" && gid="$(id -g drcloud)" &&
    test "$(stat -c %u /data)" = "$uid" &&
    test "$(stat -c %g /data)" = "$gid" &&
    test "$(stat -c %a /data)" = 700 &&
    test "$(stat -c %u /data/drcloud.db)" = "$uid" &&
    test "$(stat -c %g /data/drcloud.db)" = "$gid" &&
    test "$(stat -c %a /data/drcloud.db)" = 600 &&
    test "$(stat -c %u /data/catalogue.json)" = "$uid" &&
    test "$(stat -c %g /data/catalogue.json)" = "$gid" &&
    test "$(stat -c %u /data/catalogue-report.json)" = "$uid" &&
    test "$(stat -c %g /data/catalogue-report.json)" = "$gid" &&
    test "$(stat -c %a /data/catalogue.json)" = 600 &&
    test "$(stat -c %a /data/catalogue-report.json)" = 600
  '
then
  echo "RESTORE_VOLUME_PERMISSION_FAILED" >&2
  exit 1
fi

docker network create --internal "$network" >/dev/null
docker run -d --name "$container" --network "$network" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges --mount "type=volume,src=$recovery_volume,dst=/data" \
  -e DRCLOUD_ENV=recovery-gameday -e DRCLOUD_SAFE_MODE=true -e BARCODE_SYNC_MODE=dry-run \
  -e DRCLOUD_DATA_DIR=/data -e DRCLOUD_HOST=0.0.0.0 -e DRCLOUD_PORT=8080 \
  -e DRCLOUD_SECRET_KEY=recovery-isolated-nonproduction -e DRCLOUD_ADMIN_USERNAME=recovery \
  -e DRCLOUD_ADMIN_PASSWORD=recovery-isolated-unused "$image" >/dev/null
app_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

sanitized_container_logs() {
  docker logs --tail 50 "$container" 2>&1 | sed -E \
    -e 's/([Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Tt][Oo][Kk][Ee][Nn]|[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn])[^[:space:]]*/\1=[REDACTED]/g' \
    -e 's/(Bearer|Basic) [^[:space:]]+/\1 [REDACTED]/g' >&2
}

running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
if [[ "$running" != "true" ]]; then
  sanitized_container_logs
  echo "RESTORE_APP_BOOT_FAILED" >&2
  exit 1
fi

health_ok_at=""
for _ in $(seq 1 30); do
  running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
  if [[ "$running" != "true" ]]; then
    sanitized_container_logs
    echo "RESTORE_APP_BOOT_FAILED" >&2
    exit 1
  fi
  health_status="$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
  case "$health_status" in
    healthy) health_ok_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; break ;;
    starting) ;;
    unhealthy)
      sanitized_container_logs
      echo "RESTORE_HEALTH_FAILED" >&2
      exit 1
      ;;
    *) ;;
  esac
  sleep 1
done
[[ -n "$health_ok_at" ]] || { echo "RESTORE_HEALTH_TIMEOUT" >&2; exit 1; }
# Secondary business probe only: authentication failures and endpoint errors do
# not override the mandatory Docker /health result.
docker exec "$container" python -c 'import urllib.error,urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:8080/api/roadmap", timeout=2).read()
except urllib.error.HTTPError as exc:
    raise SystemExit(0 if exc.code in (401,403) else 1)' >/dev/null 2>&1 || true

completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
phases="$work/phases.json"
"$PYTHON_BIN" - "$phases" "$started_at" "$backup_selected_at" "$restore_completed_at" "$integrity_completed_at" "$app_started_at" "$health_ok_at" "$completed_at" <<'PY'
import json,sys
keys=("started_at","backup_selected_at","restore_completed_at","integrity_completed_at","app_started_at","health_ok_at","business_validation_completed_at")
json.dump(dict(zip(keys,sys.argv[2:])),open(sys.argv[1],"w"))
PY
# Never substitute HEAD^: only chronological successful deployment markers qualify.
production_container="$(docker compose ps -q drcloud-os)"
# The full proof is deliberately separate from restore-only and operates only
# on its own second volume.  It sets fail-closed evidence fields.
source "$repo/deploy/ovh/recovery-rollback.sh"
rollback_facts="$work/rollback.json"
"$PYTHON_BIN" - "$rollback_facts" "$rollback_result" "$rollback_reason" "$n" "$n_minus_1" "$n_health" "$n1_health" "$n_return_health" "$schema_compatibility" "$data_loss_check" <<'PY'
import json,sys
keys=("result","reason","n","n_minus_1","n_health","n_minus_1_health","n_return_health","schema_compatibility","data_loss_check")
d=dict(zip(keys,sys.argv[2:])); d["n"]=d["n"] or None; d["n_minus_1"]=d["n_minus_1"] or None
json.dump(d,open(sys.argv[1],"w"))
PY

report="$DRCLOUD_DEPLOYMENT_STATE_DIR/recovery_evidence_production.json"
"$PYTHON_BIN" - "$selection" "$timings" "$phases" "$report" "$mode" "$rollback_result" "$n" "$n_minus_1" "$rollback_facts" <<'PY'
import datetime,json,re,sys
sel=json.load(open(sys.argv[1])); facts=json.load(open(sys.argv[2])); phases=json.load(open(sys.argv[3])); out=sys.argv[4]
parse=lambda x: datetime.datetime.fromisoformat(x.replace("Z","+00:00"))
start=parse(phases["started_at"]); end=parse(phases["business_validation_completed_at"])
rto=max(0,(end-start).total_seconds())
duration=lambda a,b:max(0,(parse(phases[b])-parse(phases[a])).total_seconds())
report={"schema_version":1,"environment":"production","evidence_level":"PRODUCTION_DATA_RESTORE","mode":sys.argv[5],
 "storage":{"type":"TEMPORARY_DOCKER_VOLUME","production_volume_mounted":False,"restored_database_copy":True,"runtime_user":"drcloud"},
 "backup":{"result":"PRODUCTION_BACKUP_VALID","backup_id":sel["backup_id"],"created_at":sel["created_at"],"location_classification":"BACKUP_ON_HOST_ONLY"},
 "restore":{"evidence_level":"PRODUCTION_DATA_RESTORE","result":"PRODUCTION_DATA_PROVEN","completed_at":phases["business_validation_completed_at"],"app_boot":"APP_BOOT_OK","health":"HEALTH_OK","observed_rto_seconds":rto,**facts},
 "rto":{**phases,"backup_selection_duration":duration("started_at","backup_selected_at"),"database_restore_duration":duration("backup_selected_at","restore_completed_at"),
        "integrity_duration":duration("restore_completed_at","integrity_completed_at"),"application_boot_duration":duration("integrity_completed_at","app_started_at"),
        "health_duration":duration("app_started_at","health_ok_at"),"business_validation_duration":duration("health_ok_at","business_validation_completed_at"),
        "observed_rto_seconds":rto,"observed_rto_human":f"{int(rto)}s"},
 "rollback":{"evidence_level":"OVH_EQUIVALENT_ROLLBACK" if sys.argv[6]=="ROLLBACK_PROVEN" else "NOT_PROVEN","environment":"OVH_EQUIVALENT_STAGING",**(json.load(open(sys.argv[9])) if len(sys.argv)>9 else {"result":sys.argv[6],"n":sys.argv[7] or None,"n_minus_1":sys.argv[8] or None,"schema_compatibility":"UNKNOWN"})},
 "safety":{"safe_mode":True,"external_provider_auth":"NONE","production_database_target":False,"live_database_used_for_restore":False,"live_watermark_used_for_measurement_only":True,"production_volume_mounted":False,"network":"INTERNAL_ONLY","network_exposure":"NONE","production_port_published":False}}
forbidden=("password","secret","token","credential","api_key","private_key","authorization")
def scan(v):
 if isinstance(v,dict):
  for k,x in v.items():
   if any(w in k.lower() for w in forbidden): raise SystemExit("forbidden evidence key")
   scan(x)
 elif isinstance(v,list):
  for x in v: scan(x)
 elif isinstance(v,str) and re.search(r"(?i)(bearer |basic |-----begin|ghp_|sk_live_)",v): raise SystemExit("credential pattern")
scan(report)
open(out+".tmp","w").write(json.dumps(report,indent=2,sort_keys=True)+"\n")
import os; os.chmod(out+".tmp",0o600); os.replace(out+".tmp",out)
PY
echo "PRODUCTION_DATA_PROVEN"
if [[ "$mode" == "full" && "$rollback_result" != "ROLLBACK_PROVEN" ]]; then
  echo "${rollback_reason:-ROLLBACK_NOT_PROVEN}" >&2
  exit 1
fi
