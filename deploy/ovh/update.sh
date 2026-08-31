#!/usr/bin/env bash
set -Eeuo pipefail
target="${1:-}"
[[ "$target" =~ ^[0-9a-f]{40}$ ]] || { echo "Usage: $0 <sha-commit-40-caractères>" >&2; exit 2; }
repo="${DRCLOUD_REPO_DIR:-}"
if [[ -z "$repo" ]]; then
  repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
fi
[[ -d "$repo/.git" ]] || { echo "ERREUR: dépôt de déploiement introuvable." >&2; exit 1; }
# Initialize the canonical environment in the orchestrator, before backup or
# any other child can evaluate docker-compose.yml.
source "$repo/deploy/ovh/deployment-environment.sh"
state_dir="$DRCLOUD_DEPLOYMENT_STATE_DIR"

github_token=""
if [[ "${DRCLOUD_GITHUB_TOKEN_STDIN:-0}" == "1" ]]; then
  IFS= read -r github_token
  [[ -n "$github_token" ]] || { echo "ERREUR: jeton GitHub de lecture absent." >&2; exit 1; }
fi

record_successful_commit() {
  "$repo/deploy/ovh/deployment-state.sh" "$state_dir" "$1"
}

fetch_reviewed_main() {
  if [[ "${DRCLOUD_GITHUB_TOKEN_STDIN:-0}" == "1" ]]; then
    local auth
    auth="$(printf 'x-access-token:%s' "$github_token" | base64 | tr -d '\n')"
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0='http.https://github.com/.extraheader' \
    GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth" \
      git -C "$repo" fetch --no-tags origin main
    unset auth
    github_token=""
  else
    git -C "$repo" fetch --no-tags origin main
  fi
}

exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"
# Read-only production proofs may briefly hold this same host lock. Wait for
# them instead of failing a deployment immediately; retain a bounded timeout
# so a wedged proof cannot block deployment forever.
flock -w 300 9 || { echo "ERREUR: verrou de déploiement indisponible après 300 secondes." >&2; exit 1; }
[[ -z "$(git -C "$repo" status --porcelain)" ]] || { echo "ERREUR: arbre Git non propre; mise à jour refusée." >&2; exit 1; }
previous="$(git -C "$repo" rev-parse HEAD)"
echo "Déploiement demandé: previous=$previous target=$target"
# Back up the running data with the currently deployed, known-good code.
"$repo/deploy/ovh/backup.sh"
fetch_reviewed_main
git -C "$repo" merge-base --is-ancestor "$target" origin/main || { echo "ERREUR: la cible n'appartient pas à origin/main." >&2; exit 1; }
git -C "$repo" checkout --detach "$target"
if DRCLOUD_BUILD_COMMIT="$target" "$repo/deploy/ovh/deploy.sh" && \
   DRCLOUD_HTTPS_URL="${DRCLOUD_HTTPS_URL:-https://osdrcloud.fr}" EXPECTED_COMMIT="$target" "$repo/deploy/ovh/check.sh"; then
  if record_successful_commit "$target"; then
    echo "SUCCÈS: commit déployé, HTTPS validé et marqueur publié: $target"
    exit 0
  fi
  echo "ERREUR: application validée mais publication du marqueur impossible." >&2
fi
echo "ERREUR: déploiement de $target; rollback applicatif vers $previous (aucune restauration de données)." >&2
git -C "$repo" checkout --detach "$previous"
if DRCLOUD_BUILD_COMMIT="$previous" "$repo/deploy/ovh/deploy.sh" && \
   DRCLOUD_HTTPS_URL="${DRCLOUD_HTTPS_URL:-https://osdrcloud.fr}" EXPECTED_COMMIT="$previous" "$repo/deploy/ovh/check.sh"; then
  if record_successful_commit "$previous"; then
    echo "ROLLBACK RÉUSSI: commit=$previous. La sauvegarde n'a pas été restaurée." >&2
  else
    echo "ROLLBACK APPLICATIF RÉUSSI, mais publication du marqueur impossible: commit=$previous." >&2
  fi
else
  echo "ROLLBACK EN ÉCHEC: intervention d'urgence requise; données laissées intactes." >&2
fi
exit 1
