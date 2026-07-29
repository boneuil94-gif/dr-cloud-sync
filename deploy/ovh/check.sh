#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker info >/dev/null
docker compose ps --status running drcloud-os | grep -q drcloud-os
curl --fail --silent --show-error http://127.0.0.1:8080/health
docker volume inspect drcloud-data >/dev/null
df -h / /var/lib/docker
usage="$(df --output=pcent /var/lib/docker | tail -1 | tr -dc '0-9')"
(( usage < 90 )) || { echo "Espace Docker critique: ${usage}%" >&2; exit 1; }
if [[ -n "${DRCLOUD_HTTPS_URL:-}" ]]; then curl --fail --silent --show-error "$DRCLOUD_HTTPS_URL/health"; fi
echo "Contrôles locaux réussis. Définir DRCLOUD_HTTPS_URL=https://os.drcloud.fr après DNS et HTTPS réels."
