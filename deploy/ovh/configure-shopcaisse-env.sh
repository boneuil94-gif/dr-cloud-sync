#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
env_file="${DRCLOUD_ENV_FILE:-$script_dir/drcloud.env}"
[[ -f "$env_file" ]] || { echo "ERREUR: environnement runtime absent." >&2; exit 1; }
IFS= read -r api_key || { echo "ERREUR: clé ShopCaisse absente." >&2; exit 1; }
[[ -n "$api_key" ]] || { echo "ERREUR: clé ShopCaisse vide." >&2; exit 1; }

umask 077
tmp="$(mktemp "$script_dir/.drcloud.env.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT
written=false
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    SHOPCAISSE_API_KEY=*)
      $written || printf 'SHOPCAISSE_API_KEY=%s\n' "$api_key" >> "$tmp"
      written=true
      ;;
    *) printf '%s\n' "$line" >> "$tmp" ;;
  esac
done < "$env_file"
$written || printf 'SHOPCAISSE_API_KEY=%s\n' "$api_key" >> "$tmp"
chmod 0600 "$tmp"
mv -f -- "$tmp" "$env_file"
trap - EXIT
echo "Configuration ShopCaisse runtime installée."
