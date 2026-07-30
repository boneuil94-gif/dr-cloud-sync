#!/usr/bin/env bash
set -Eeuo pipefail

[[ "$(id -u)" == 0 ]] || { echo "ERREUR: exécuter cette préparation unique en root." >&2; exit 1; }
deploy_user="${DRCLOUD_DEPLOY_USER:-drcloud-deploy}"
deploy_group="${DRCLOUD_DEPLOY_GROUP:-drcloud-deploy}"
state_dir="${DRCLOUD_DEPLOYMENT_STATE_DIR:-/opt/drcloud-os/deploy/ovh/.deployment-state}"
[[ "$state_dir" == /* ]] || { echo "ERREUR: DRCLOUD_DEPLOYMENT_STATE_DIR doit être absolu." >&2; exit 2; }
getent passwd "$deploy_user" >/dev/null || { echo "ERREUR: utilisateur absent: $deploy_user" >&2; exit 1; }
getent group "$deploy_group" >/dev/null || { echo "ERREUR: groupe absent: $deploy_group" >&2; exit 1; }

# Idempotently repair an existing production directory as well as create it.
install -d -o "$deploy_user" -g "$deploy_group" -m 0755 "$state_dir"
chown "$deploy_user:$deploy_group" "$state_dir"
chmod 0755 "$state_dir"
echo "État de déploiement prêt: $state_dir ($deploy_user:$deploy_group 0755)"
