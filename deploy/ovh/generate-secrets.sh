#!/usr/bin/env bash
set -euo pipefail

secret_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
admin_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
output="${1:-}"

echo "ATTENTION : NE PAS COMMITER CE FICHIER. Ne partagez jamais ces valeurs."
if [[ -z "$output" ]]; then
  printf 'DRCLOUD_SECRET_KEY=%s\nDRCLOUD_ADMIN_PASSWORD=%s\n' "$secret_key" "$admin_password"
  echo "Copiez-les manuellement dans deploy/ovh/drcloud.env (ignoré par Git)."
  exit 0
fi
if [[ -e "$output" ]]; then
  echo "Refus d'écraser le fichier existant : $output" >&2
  exit 1
fi
umask 077
cp "$(dirname "$0")/drcloud.env.example" "$output"
python3 - "$output" "$secret_key" "$admin_password" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
text = text.replace("DRCLOUD_SECRET_KEY=CHANGE_ME", f"DRCLOUD_SECRET_KEY={sys.argv[2]}")
text = text.replace("DRCLOUD_ADMIN_PASSWORD=CHANGE_ME", f"DRCLOUD_ADMIN_PASSWORD={sys.argv[3]}")
p.write_text(text)
PY
chmod 600 "$output"
echo "Fichier créé avec permissions 600 : $output"
echo "Renseignez encore le nom admin et, uniquement si nécessaires, les connecteurs. NE PAS COMMITER CE FICHIER."
