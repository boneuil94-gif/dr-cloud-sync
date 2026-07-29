#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
docker info >/dev/null
docker compose ps --status running drcloud-os | grep -q drcloud-os
check_health() {
  local url="$1" expected="${EXPECTED_COMMIT:-}" body
  body="$(curl --fail --silent --show-error --retry 3 --retry-delay 2 "$url/health")"
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok" and d["database"]=="ok"; expected=sys.argv[1]; assert not expected or d["commit"]==expected' "$expected" <<<"$body"
  echo "Health OK: $url commit=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])' <<<"$body")"
}
check_health http://127.0.0.1:8080
docker volume inspect drcloud-data >/dev/null
df -h / /var/lib/docker
usage="$(df --output=pcent /var/lib/docker | tail -1 | tr -dc '0-9')"
(( usage < 90 )) || { echo "ERREUR: espace Docker critique: ${usage}%" >&2; exit 1; }
check_health "${DRCLOUD_HTTPS_URL:-https://osdrcloud.fr}"
echo "Contrôles local, disque et HTTPS réussis."
