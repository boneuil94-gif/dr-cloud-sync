#!/usr/bin/env bash
set -Eeuo pipefail

state_dir="${1:-}"
commit="${2:-}"
[[ "$state_dir" == /* ]] || { echo "ERREUR: le chemin d'état doit être absolu." >&2; exit 2; }
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "ERREUR: SHA de déploiement invalide." >&2; exit 2; }
[[ -d "$state_dir" ]] || { echo "ERREUR: répertoire d'état absent: $state_dir" >&2; exit 1; }

# The SSH deployment account is the sole host-side writer.  A 0755 directory
# lets the unprivileged container user traverse/read the bind mount without
# granting it any host write permission (Compose additionally mounts it :ro).
expected_user="${DRCLOUD_DEPLOY_USER:-drcloud-deploy}"
expected_group="${DRCLOUD_DEPLOY_GROUP:-drcloud-deploy}"
actual_user="$(id -un)"
actual_uid="$(id -u)"
actual_group_gid="$(getent group "$expected_group" | cut -d: -f3 || true)"
owner_uid="$(stat -c %u "$state_dir")"
owner_gid="$(stat -c %g "$state_dir")"
mode="$(stat -c %a "$state_dir")"
[[ "$actual_user" == "$expected_user" ]] || {
  echo "ERREUR: writer=$actual_user; utilisateur requis=$expected_user." >&2; exit 1;
}
[[ -n "$actual_group_gid" && "$owner_uid" == "$actual_uid" && "$owner_gid" == "$actual_group_gid" && "$mode" == 755 ]] || {
  echo "ERREUR: permissions de $state_dir incompatibles; exécuter prepare-deployment-state.sh une fois." >&2; exit 1;
}

old_commit=""
if [[ -f "$state_dir/last-successful-commit" ]]; then
  old_commit="$(cat "$state_dir/last-successful-commit")"
  [[ "$old_commit" =~ ^[0-9a-f]{40}$ ]] || {
    echo "ERREUR: marqueur known-good existant invalide; historique non modifié." >&2; exit 1;
  }
fi

# Build the chronological known-good history exclusively from successful
# deployment markers.  In particular, never infer a predecessor from Git.
history_tmp="$(mktemp "$state_dir/.successful-commit-history.XXXXXX")" || exit 1
tmp="$(mktemp "$state_dir/.last-successful-commit.XXXXXX")" || {
  echo "ERREUR: impossible de créer le marqueur temporaire dans $state_dir." >&2; exit 1;
}
trap 'rm -f -- "$tmp" "$history_tmp"' EXIT
{
  if [[ -f "$state_dir/successful-commit-history" ]]; then
    while IFS= read -r sha; do
      [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "ERREUR: historique known-good invalide." >&2; exit 1; }
      printf '%s\n' "$sha"
    done < "$state_dir/successful-commit-history"
  fi
  # Always carry the previous marker forward.  Deduplication below makes this
  # safe when it is already the final history entry.
  [[ -z "$old_commit" ]] || printf '%s\n' "$old_commit"
  printf '%s\n' "$commit"
} | awk 'NR == 1 || $0 != previous { lines[++n]=$0 } { previous=$0 } END { start=n-19; if(start<1)start=1; for(i=start;i<=n;i++)print lines[i] }' > "$history_tmp"

printf '%s\n' "$commit" > "$tmp"
chmod 0444 "$tmp" "$history_tmp"
# Publish history before N.  A crash can at worst leave a valid history whose
# final entry is newer than the marker; readers require both to agree.
mv -f -- "$history_tmp" "$state_dir/successful-commit-history"
mv -f -- "$tmp" "$state_dir/last-successful-commit"
trap - EXIT
