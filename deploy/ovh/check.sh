#!/usr/bin/env bash
set -Eeuo pipefail
script_dir="$(cd -P -- "$(dirname "$0")" && pwd)"
source "$script_dir/deployment-environment.sh"
cd "$script_dir"
docker info >/dev/null
docker compose ps --status running drcloud-os | grep -q drcloud-os
worker_id="$(docker compose ps --quiet --status running automation-worker)"
[[ -n "$worker_id" ]] || { echo "ERREUR: automation-worker absent." >&2; exit 1; }
check_health() {
  local url="$1" expected="${EXPECTED_COMMIT:-}" body
  body="$(curl --fail --silent --show-error --retry 3 --retry-delay 2 "$url/health")"
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok" and d["database"]=="ok"; expected=sys.argv[1]; assert not expected or d["commit"]==expected' "$expected" <<<"$body"
  echo "Health OK: $url commit=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])' <<<"$body")"
}
check_health http://127.0.0.1:8080
docker volume inspect drcloud-data >/dev/null
web_policy="$(docker compose exec -T drcloud-os python -c 'import hashlib,os; value=os.environ.get("PRESTASHOP_PAID_STATE_IDS",""); assert value; print(hashlib.sha256(value.encode()).hexdigest())')"
worker_policy="$(docker compose exec -T automation-worker python -c 'import hashlib,os; value=os.environ.get("PRESTASHOP_PAID_STATE_IDS",""); assert value; print(hashlib.sha256(value.encode()).hexdigest())')"
[[ "$web_policy" == "$worker_policy" ]] || { echo "ERREUR: policy PrestaShop différente entre web et worker." >&2; exit 1; }
web_shopcaisse_key="$(docker compose exec -T drcloud-os python -c 'import hashlib,os; value=os.environ.get("SHOPCAISSE_API_KEY",""); assert value; print(hashlib.sha256(value.encode()).hexdigest())')"
worker_shopcaisse_key="$(docker compose exec -T automation-worker python -c 'import hashlib,os; value=os.environ.get("SHOPCAISSE_API_KEY",""); assert value; print(hashlib.sha256(value.encode()).hexdigest())')"
[[ "$web_shopcaisse_key" == "$worker_shopcaisse_key" ]] || { echo "ERREUR: configuration ShopCaisse différente entre web et worker." >&2; exit 1; }
check_qonto_runtime() {
  local service="$1" label="$2"
  docker compose exec -T -e "DRCLOUD_QONTO_LABEL=$label" "$service" python -c 'import os; from dr_cloud_sync.qonto import EnvironmentSecretProvider; reference=os.environ.get("QONTO_CREDENTIAL_REF",""); key=reference.removeprefix("env:") if reference.startswith("env:") else ""; key_present=bool(key and os.environ.get(key)); resolved=bool(EnvironmentSecretProvider(os.environ).resolve(reference)); answer=lambda condition: "OUI" if condition else "NON"; print(os.environ["DRCLOUD_QONTO_LABEL"]+" :"); print("QONTO_CREDENTIAL_REF présent : "+answer(reference)); print("QONTO_CREDENTIAL présent : "+answer(key_present)); print("reference configured : "+answer(reference)); print("environment key present : "+answer(key_present)); print("resolved : "+answer(resolved)); assert reference == "env:QONTO_CREDENTIAL" and key_present and resolved' || { echo "QONTO_CREDENTIAL n'a pas été injecté dans le runtime" >&2; exit 1; }
}
check_qonto_runtime drcloud-os Web
check_qonto_runtime automation-worker Worker
df -h / /var/lib/docker
usage="$(df --output=pcent /var/lib/docker | tail -1 | tr -dc '0-9')"
(( usage < 90 )) || { echo "ERREUR: espace Docker critique: ${usage}%" >&2; exit 1; }
check_health "${DRCLOUD_HTTPS_URL:-https://osdrcloud.fr}"
echo "Contrôles local, disque et HTTPS réussis."
