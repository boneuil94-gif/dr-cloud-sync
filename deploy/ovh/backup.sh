#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
source "$script_dir/deployment-environment.sh"
cd "$script_dir"
docker compose exec -T drcloud-os dr-cloud-sync os-backup
echo "Sauvegarde applicative créée dans le volume persistant drcloud-data. Copier ensuite une copie chiffrée hors VPS; le backup OVH ne la remplace pas."
