#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
install -d -m 0750 "${DRCLOUD_BACKUP_ROOT:-/var/backups/drcloud}"
docker compose exec -T -e DRCLOUD_BACKUP_DIR=/backups drcloud-os dr-cloud-sync os-backup
echo "Sauvegarde applicative créée hors volume. Copier ensuite une copie chiffrée hors VPS; le backup OVH ne la remplace pas."
