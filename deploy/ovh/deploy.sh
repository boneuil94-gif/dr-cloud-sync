#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
env_file=drcloud.env
[[ -f "$env_file" ]] || { echo "Créer $PWD/$env_file depuis le modèle." >&2; exit 1; }
chmod 600 "$env_file"
value() { sed -n "s/^$1=//p" "$env_file" | tail -1; }
[[ "$(value DRCLOUD_SAFE_MODE)" == true ]] || { echo "DRCLOUD_SAFE_MODE doit être true." >&2; exit 1; }
[[ "$(value BARCODE_SYNC_MODE)" == dry-run ]] || { echo "BARCODE_SYNC_MODE doit être dry-run." >&2; exit 1; }
for key in DRCLOUD_SECRET_KEY DRCLOUD_ADMIN_USERNAME DRCLOUD_ADMIN_PASSWORD; do
  val="$(value "$key")"; [[ -n "$val" && "$val" != CHANGE_ME ]] || { echo "$key doit être renseigné." >&2; exit 1; }
done
docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans
for attempt in {1..30}; do
  if curl --silent --show-error --fail http://127.0.0.1:8080/health >/dev/null; then
    docker compose ps
    echo "DrCloud OS est sain en local (SAFE_MODE=true, dry-run)."
    exit 0
  fi
  sleep 2
done
docker compose ps
docker compose logs --tail=50 drcloud-os
echo "Échec du health check." >&2
exit 1
