#!/usr/bin/env bash
set -Eeuo pipefail
script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
repo="$(git -C "$script_dir" rev-parse --show-toplevel)"
source "$script_dir/deployment-environment.sh"
cd "$script_dir"
env_file=drcloud.env
[[ -f "$env_file" ]] || { echo "ERREUR: créer $PWD/$env_file depuis drcloud.env.example." >&2; exit 1; }
chmod 600 "$env_file"
value() { sed -n "s/^$1=//p" "$env_file" | tail -1; }
[[ "$(value DRCLOUD_SAFE_MODE)" == true ]] || { echo "ERREUR: DRCLOUD_SAFE_MODE doit être true." >&2; exit 1; }
[[ "$(value BARCODE_SYNC_MODE)" == dry-run ]] || { echo "ERREUR: BARCODE_SYNC_MODE doit être dry-run." >&2; exit 1; }
for key in DRCLOUD_SECRET_KEY DRCLOUD_ADMIN_USERNAME DRCLOUD_ADMIN_PASSWORD SHOPCAISSE_API_KEY; do
  val="$(value "$key")"; [[ -n "$val" && "$val" != CHANGE_ME ]] || { echo "ERREUR: $key doit être renseigné." >&2; exit 1; }
done
export DRCLOUD_BUILD_COMMIT="${DRCLOUD_BUILD_COMMIT:-$(git -C "$repo" rev-parse HEAD)}"
export DRCLOUD_BUILD_DATE="${DRCLOUD_BUILD_DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
# Only this small runtime directory is shared with the container.  The mount is
# read-only there; update.sh publishes its marker atomically after validation.
docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans
for attempt in {1..60}; do
  health="$(curl --silent --show-error --fail http://127.0.0.1:8080/health 2>/dev/null || true)"
  if [[ -n "$health" ]] && python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok" and d["commit"]==sys.argv[1]' "$DRCLOUD_BUILD_COMMIT" <<<"$health"; then
    docker compose ps
    echo "SUCCÈS: DrCloud OS sain; commit=$DRCLOUD_BUILD_COMMIT"
    exit 0
  fi
  sleep 2
done
docker compose ps
docker compose logs --tail=100 drcloud-os
echo "ERREUR: health check local ou vérification du commit en échec." >&2
exit 1
