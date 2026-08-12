#!/usr/bin/env bash
# Sourced by recovery-gameday.sh after the independent restore proof.
rollback_result="NOT_REQUESTED"; rollback_reason=""; n_minus_1=""
n_health="NOT_EXECUTED"; n1_health="NOT_EXECUTED"; n_return_health="NOT_EXECUTED"
schema_compatibility="UNKNOWN"; data_loss_check="NOT_EXECUTED"
n="$(cat "$DRCLOUD_DEPLOYMENT_STATE_DIR/last-successful-commit" 2>/dev/null || true)"

rollback_start() {
  local name="$1" run_image="$2"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --network "$rollback_network" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL --security-opt no-new-privileges --mount "type=volume,src=$rollback_volume,dst=/data" \
    -e DRCLOUD_ENV=recovery-gameday -e DRCLOUD_SAFE_MODE=true -e BARCODE_SYNC_MODE=dry-run \
    -e DRCLOUD_DATA_DIR=/data -e DRCLOUD_HOST=0.0.0.0 -e DRCLOUD_PORT=8080 \
    -e DRCLOUD_SECRET_KEY=recovery-isolated-nonproduction -e DRCLOUD_ADMIN_USERNAME=recovery \
    -e DRCLOUD_ADMIN_PASSWORD=recovery-isolated-unused "$run_image" >/dev/null
}
rollback_health() {
  local name="$1" phase="$2" running health
  for _ in $(seq 1 90); do
    running="$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null || true)"
    [[ "$running" == true ]] || { echo "${phase}_BOOT_FAILED" >&2; return 1; }
    health="$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null || true)"
    [[ "$health" == healthy ]] && { echo "${phase}_BOOT_OK"; echo "${phase}_HEALTH_OK"; return 0; }
    [[ "$health" != unhealthy ]] || { echo "${phase}_HEALTH_FAILED" >&2; return 1; }
    sleep 1
  done
  echo "${phase}_HEALTH_TIMEOUT" >&2; return 1
}
rollback_facts() {
  local destination="$2" temporary
  temporary="$(mktemp "${destination}.tmp.XXXXXX")" || return 1
  if ! docker exec -i "$1" python - > "$temporary" <<'PY'
import hashlib,json,sqlite3
allowed=("bank_transactions","sumup_transactions","sumup_payouts","sales","sale_events","crm_customers","purchase_orders","stock_movements")
with sqlite3.connect("file:/data/drcloud.db?mode=ro",uri=True) as db:
 q=db.execute("pragma quick_check").fetchone()[0]; i=db.execute("pragma integrity_check").fetchone()[0]
 fk=list(db.execute("pragma foreign_key_check"))
 schema="\n".join((r[0] or "") for r in db.execute("select sql from sqlite_master where sql is not null order by type,name"))
 tables={r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
 counts={}; fingerprints={}
 for table in allowed:
  if table not in tables: continue
  counts[table]=db.execute(f'select count(*) from "{table}"').fetchone()[0]
  keys=[r[1] for r in db.execute(f'pragma table_info("{table}")') if r[5]]; digest=hashlib.sha256()
  if keys:
   cols=','.join('"'+k.replace('"','""')+'"' for k in keys)
   for row in db.execute(f'select {cols} from "{table}" order by {cols}'):
    digest.update(json.dumps(row,separators=(",",":"),default=str).encode()+b"\n")
  fingerprints[table]=digest.hexdigest()
 result={"quick_check":q,"integrity_check":i,"foreign_key_check":"OK" if not fk else "FAILED","schema_fingerprint":hashlib.sha256(schema.encode()).hexdigest(),"table_counts":counts,"primary_key_fingerprint":fingerprints}
 if q!="ok" or i!="ok" or fk: raise SystemExit("database integrity failed")
 print(json.dumps(result,sort_keys=True))
PY
  then
    rm -f -- "$temporary"
    return 1
  fi
  if [[ ! -s "$temporary" ]] || ! "$PYTHON_BIN" - "$temporary" <<'PY'
import json,sys
required={"quick_check","integrity_check","foreign_key_check","schema_fingerprint","table_counts","primary_key_fingerprint"}
with open(sys.argv[1], encoding="utf-8") as source:
 data=json.load(source)
if not isinstance(data,dict) or not required.issubset(data):
 raise SystemExit("rollback fact snapshot is missing required keys")
PY
  then
    rm -f -- "$temporary"
    return 1
  fi
  mv -f -- "$temporary" "$destination"
}

[[ "$mode" == full ]] || return 0
rollback_result="ROLLBACK_NOT_PROVEN"
history="$DRCLOUD_DEPLOYMENT_STATE_DIR/successful-commit-history"
mapfile -t known_good < <(tail -n 2 "$history" 2>/dev/null || true)
if (( ${#known_good[@]} < 2 )); then rollback_reason="ROLLBACK_HISTORY_INSUFFICIENT"
else
  n_minus_1="${known_good[0]}"; history_n="${known_good[1]}"
  [[ "$n" =~ ^[0-9a-f]{40}$ && "$n_minus_1" =~ ^[0-9a-f]{40}$ && "$history_n" == "$n" && "$n_minus_1" != "$n" ]] || rollback_reason="ROLLBACK_HISTORY_INSUFFICIENT"
fi
n_image_id="$(docker inspect --format '{{.Image}}' "$production_container" 2>/dev/null || true)"
image_commit="$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$n_image_id" 2>/dev/null | sed -n 's/^DRCLOUD_BUILD_COMMIT=//p')"
[[ -n "$rollback_reason" || ( "$n_image_id" =~ ^sha256:[0-9a-f]{64}$ && "$image_commit" == "$n" ) ]] || rollback_reason="ROLLBACK_N_IMAGE_MISMATCH"
[[ -n "$rollback_reason" ]] || git -C "$repo" cat-file -e "$n_minus_1^{commit}" 2>/dev/null || rollback_reason="ROLLBACK_N_MINUS_1_UNKNOWN"
[[ -n "$rollback_reason" ]] || git -C "$repo" merge-base --is-ancestor "$n_minus_1" refs/remotes/origin/main || rollback_reason="ROLLBACK_N_MINUS_1_NOT_ON_ORIGIN_MAIN"
worktree="$work/n-minus-1-src"
[[ -n "$rollback_reason" ]] || git -C "$repo" worktree add --detach "$worktree" "$n_minus_1" >/dev/null || rollback_reason="ROLLBACK_N_MINUS_1_CHECKOUT_FAILED"
[[ -n "$rollback_reason" ]] || docker build --pull=false --build-arg "DRCLOUD_BUILD_COMMIT=$n_minus_1" --build-arg "DRCLOUD_BUILD_DATE=$started_at" --tag "$n1_image" --file "$worktree/Dockerfile" "$worktree" >/dev/null || rollback_reason="ROLLBACK_N_MINUS_1_BUILD_FAILED"
if [[ -z "$rollback_reason" ]]; then
  docker volume create "$rollback_volume" >/dev/null; docker network create --internal "$rollback_network" >/dev/null
  # The seed is exclusively the checksum-validated backup copy, never live data.
  docker run --rm --user 0:0 --network none --read-only --mount "type=volume,src=$rollback_volume,dst=/data" \
    --mount "type=bind,src=$work/restored-data,dst=/seed,readonly" --entrypoint /bin/sh "$n_image_id" -c \
    'cp /seed/drcloud.db /seed/catalogue.json /seed/catalogue-report.json /data/ && if [ -d /seed/media ]; then cp -R /seed/media /data/media; fi && chown -R drcloud:drcloud /data && chmod 700 /data && find /data -type f -exec chmod 600 {} +' || rollback_reason="ROLLBACK_SEED_FAILED"
fi
before="$work/rollback-before.json"; middle="$work/rollback-middle.json"; returned="$work/rollback-return.json"
if [[ -z "$rollback_reason" ]]; then
  if rollback_start "$n_container" "$n_image_id" && rollback_health "$n_container" N; then
    n_health="HEALTH_OK"
    rollback_facts "$n_container" "$before" || rollback_reason="ROLLBACK_FACT_CAPTURE_FAILED"
  else
    rollback_reason="ROLLBACK_N_HEALTH_FAILED"
  fi
  docker rm -f "$n_container" >/dev/null 2>&1 || true
fi
if [[ -z "$rollback_reason" ]]; then
  if rollback_start "$n1_container" "$n1_image" && rollback_health "$n1_container" N_MINUS_1; then
    n1_health="HEALTH_OK"; schema_compatibility="COMPATIBLE"
    rollback_facts "$n1_container" "$middle" || rollback_reason="ROLLBACK_FACT_CAPTURE_FAILED"
  else
    rollback_reason="ROLLBACK_SCHEMA_INCOMPATIBLE"; schema_compatibility="INCOMPATIBLE"
  fi
  docker rm -f "$n1_container" >/dev/null 2>&1 || true
fi
if [[ -z "$rollback_reason" ]]; then
  if rollback_start "$n_container" "$n_image_id" && rollback_health "$n_container" N_RETURN; then
    n_return_health="HEALTH_OK"
    rollback_facts "$n_container" "$returned" || rollback_reason="ROLLBACK_FACT_CAPTURE_FAILED"
  else
    rollback_reason="ROLLBACK_N_RETURN_HEALTH_FAILED"
  fi
  docker rm -f "$n_container" >/dev/null 2>&1 || true
fi
if [[ -z "$rollback_reason" ]]; then
  comparison_status=0
  "$PYTHON_BIN" - "$before" "$middle" "$returned" <<'PY' || comparison_status=$?
import json,sys
required={"quick_check","integrity_check","foreign_key_check","schema_fingerprint","table_counts","primary_key_fingerprint"}
try:
 before,middle,returned=(json.load(open(p)) for p in sys.argv[1:])
 if any(not isinstance(snapshot,dict) or not required.issubset(snapshot) for snapshot in (before,middle,returned)):
  raise ValueError("invalid rollback fact snapshot")
except (OSError,ValueError,TypeError,json.JSONDecodeError) as error:
 print(f"rollback fact validation failed: {error}",file=sys.stderr)
 raise SystemExit(2)
for later in (middle,returned):
 for table,count in before["table_counts"].items():
  if later["table_counts"].get(table,-1)<count: raise SystemExit("row count decreased")
  if later["primary_key_fingerprint"].get(table)!=before["primary_key_fingerprint"].get(table): raise SystemExit("business primary keys changed")
if returned["schema_fingerprint"]!=before["schema_fingerprint"]: raise SystemExit("N schema not restored")
PY
  if (( comparison_status == 0 )); then
    data_loss_check="PASS"; rollback_result="ROLLBACK_PROVEN"
  elif (( comparison_status == 2 )); then
    data_loss_check="NOT_EXECUTED"; rollback_reason="ROLLBACK_FACT_CAPTURE_FAILED"
  else
    data_loss_check="FAIL"; rollback_reason="ROLLBACK_DATA_LOSS_DETECTED"
  fi
fi
