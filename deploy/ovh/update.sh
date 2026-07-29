#!/usr/bin/env bash
set -euo pipefail
target="${1:-}"
[[ -n "$target" ]] || { echo "Usage: $0 <tag-ou-commit>" >&2; exit 2; }
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
[[ -z "$(git -C "$repo" status --porcelain)" ]] || { echo "Arbre Git non propre; mise à jour refusée." >&2; exit 1; }
previous="$(git -C "$repo" rev-parse HEAD)"
git -C "$repo" fetch --tags origin
git -C "$repo" rev-parse --verify "${target}^{commit}" >/dev/null
git -C "$repo" checkout --detach "$target"
"$repo/deploy/ovh/backup.sh"
if ! "$repo/deploy/ovh/deploy.sh"; then
  echo "Échec. Rollback applicatif vers $previous (la DB et le volume ne sont pas supprimés)." >&2
  git -C "$repo" checkout --detach "$previous"
  "$repo/deploy/ovh/deploy.sh"
  exit 1
fi
echo "Mise à jour réussie. Commit précédent pour rollback contrôlé : $previous"
