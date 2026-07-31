#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
env_file="${DRCLOUD_ENV_FILE:-$script_dir/drcloud.env}"
expected_url="https://dr-cloudshop.com/api"

[[ -f "$env_file" ]] || { echo "ERREUR: environnement runtime absent." >&2; exit 1; }
IFS= read -r api_url || { echo "ERREUR: URL PrestaShop absente." >&2; exit 1; }
IFS= read -r api_key || { echo "ERREUR: clé PrestaShop absente." >&2; exit 1; }
[[ "$api_url" == "$expected_url" ]] || { echo "ERREUR: URL PrestaShop inattendue." >&2; exit 1; }
[[ -n "$api_key" ]] || { echo "ERREUR: clé PrestaShop vide." >&2; exit 1; }

umask 077
tmp="$(mktemp "$script_dir/.drcloud.env.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT
url_written=false
key_written=false
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    PRESTASHOP_API_URL=*)
      $url_written || printf 'PRESTASHOP_API_URL=%s\n' "$api_url" >> "$tmp"
      url_written=true
      ;;
    PRESTASHOP_API_KEY=*)
      $key_written || printf 'PRESTASHOP_API_KEY=%s\n' "$api_key" >> "$tmp"
      key_written=true
      ;;
    *) printf '%s\n' "$line" >> "$tmp" ;;
  esac
done < "$env_file"
$url_written || printf 'PRESTASHOP_API_URL=%s\n' "$api_url" >> "$tmp"
$key_written || printf 'PRESTASHOP_API_KEY=%s\n' "$api_key" >> "$tmp"
chmod 0600 "$tmp"
mv -f -- "$tmp" "$env_file"
trap - EXIT
echo "Configuration PrestaShop runtime installée."
