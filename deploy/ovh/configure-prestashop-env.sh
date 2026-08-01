#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
env_file="${DRCLOUD_ENV_FILE:-$script_dir/drcloud.env}"
expected_url="https://dr-cloudshop.com/api"

[[ -f "$env_file" ]] || { echo "ERREUR: environnement runtime absent." >&2; exit 1; }
IFS= read -r api_url || { echo "ERREUR: URL PrestaShop absente." >&2; exit 1; }
IFS= read -r api_key || { echo "ERREUR: clé PrestaShop absente." >&2; exit 1; }
IFS= read -r paid_states || { echo "ERREUR: états payés PrestaShop absents." >&2; exit 1; }
[[ "$api_url" == "$expected_url" ]] || { echo "ERREUR: URL PrestaShop inattendue." >&2; exit 1; }
[[ -n "$api_key" ]] || { echo "ERREUR: clé PrestaShop vide." >&2; exit 1; }
paid_states="$(python3 - "$paid_states" <<'PY'
import sys
raw=sys.argv[1].strip()
if not raw:
    raise SystemExit("ERREUR: PRESTASHOP_PAID_STATE_IDS est absent.")
states=[]
for item in raw.split(","):
    value=item.strip()
    if not value.isascii() or not value.isdigit() or int(value)<1:
        raise SystemExit("ERREUR: PRESTASHOP_PAID_STATE_IDS invalide.")
    normalized=str(int(value))
    if normalized not in states: states.append(normalized)
print(",".join(states))
PY
)"

umask 077
tmp="$(mktemp "$script_dir/.drcloud.env.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT
url_written=false
key_written=false
states_written=false
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
    PRESTASHOP_PAID_STATE_IDS=*)
      $states_written || printf 'PRESTASHOP_PAID_STATE_IDS=%s\n' "$paid_states" >> "$tmp"
      states_written=true
      ;;
    *) printf '%s\n' "$line" >> "$tmp" ;;
  esac
done < "$env_file"
$url_written || printf 'PRESTASHOP_API_URL=%s\n' "$api_url" >> "$tmp"
$key_written || printf 'PRESTASHOP_API_KEY=%s\n' "$api_key" >> "$tmp"
$states_written || printf 'PRESTASHOP_PAID_STATE_IDS=%s\n' "$paid_states" >> "$tmp"
chmod 0600 "$tmp"
mv -f -- "$tmp" "$env_file"
trap - EXIT
echo "Configuration PrestaShop runtime installée."
