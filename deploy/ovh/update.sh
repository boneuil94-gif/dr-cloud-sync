#!/usr/bin/env bash
set -Eeuo pipefail
target="${1:-}"
[[ "$target" =~ ^[0-9a-f]{40}$ ]] || { echo "Usage: $0 <sha-commit-40-caractères>" >&2; exit 2; }
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"
flock -n 9 || { echo "ERREUR: un déploiement est déjà en cours." >&2; exit 1; }
[[ -z "$(git -C "$repo" status --porcelain)" ]] || { echo "ERREUR: arbre Git non propre; mise à jour refusée." >&2; exit 1; }
previous="$(git -C "$repo" rev-parse HEAD)"
echo "Déploiement demandé: previous=$previous target=$target"
# Back up the running data with the currently deployed, known-good code.
"$repo/deploy/ovh/backup.sh"
git -C "$repo" fetch --no-tags origin main
git -C "$repo" merge-base --is-ancestor "$target" origin/main || { echo "ERREUR: la cible n'appartient pas à origin/main." >&2; exit 1; }
git -C "$repo" checkout --detach "$target"
if DRCLOUD_BUILD_COMMIT="$target" "$repo/deploy/ovh/deploy.sh" && \
   DRCLOUD_HTTPS_URL="${DRCLOUD_HTTPS_URL:-https://osdrcloud.fr}" EXPECTED_COMMIT="$target" "$repo/deploy/ovh/check.sh"; then
  printf '%s\n' "$target" > "$repo/deploy/ovh/.last-successful-commit"
  echo "SUCCÈS: commit déployé et HTTPS validé: $target"
  exit 0
fi
echo "ERREUR: déploiement de $target; rollback applicatif vers $previous (aucune restauration de données)." >&2
git -C "$repo" checkout --detach "$previous"
if DRCLOUD_BUILD_COMMIT="$previous" "$repo/deploy/ovh/deploy.sh" && \
   DRCLOUD_HTTPS_URL="${DRCLOUD_HTTPS_URL:-https://osdrcloud.fr}" EXPECTED_COMMIT="$previous" "$repo/deploy/ovh/check.sh"; then
  echo "ROLLBACK RÉUSSI: commit=$previous. La sauvegarde n'a pas été restaurée." >&2
else
  echo "ROLLBACK EN ÉCHEC: intervention d'urgence requise; données laissées intactes." >&2
fi
exit 1
