#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
env_file="${DRCLOUD_ENV_FILE:-$script_dir/drcloud.env}"
legacy=/app/docs/drcloud-os-roadmap.json
current=/app/config/roadmap_v3.json

[[ -f "$env_file" ]] || { echo "ERREUR: fichier environnement absent: $env_file" >&2; exit 1; }
tmp="$(mktemp "$(dirname "$env_file")/.drcloud.env.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

awk -v legacy="$legacy" -v current="$current" '
  BEGIN { found=0 }
  /^DRCLOUD_ROADMAP=/ {
    found=1
    if ($0 == "DRCLOUD_ROADMAP=" legacy) print "DRCLOUD_ROADMAP=" current
    else print
    next
  }
  { print }
  END { if (!found) print "DRCLOUD_ROADMAP=" current }
' "$env_file" > "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$env_file"
trap - EXIT

effective="$(sed -n 's/^DRCLOUD_ROADMAP=//p' "$env_file" | tail -1)"
[[ "$effective" == "$current" ]] || {
  echo "ERREUR: DRCLOUD_ROADMAP doit cibler $current (configuration personnalisée non modifiée)." >&2
  exit 1
}
echo "Configuration roadmap validée: effective_path=$effective"
