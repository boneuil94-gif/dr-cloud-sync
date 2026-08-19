#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# The proof can be copied to /tmp by the workflow, so anchor deployment
# configuration to the deployed repository rather than to this script's path.
# Do not expose diagnostics from the sourced helper: only this stable code is
# permitted in proof logs.
if [[ "${1:-}" != "--sanitize" ]]; then
  proof_repo="${DRCLOUD_PROOF_REPO:-/opt/drcloud-os}"
  if ! source "$proof_repo/deploy/ovh/deployment-environment.sh" >/dev/null 2>&1; then
    printf '%s\n' 'PRODUCTION_COMPOSE_ENV_UNAVAILABLE' >&2
    exit 1
  fi
  unset proof_repo
fi

# This program deliberately has one output: a sanitized JSON document.  All
# inspection (including error handling) happens in Python so secret values can
# never be expanded into a shell command line or diagnostic.
exec python3 - "$@" <<'PY'
from __future__ import annotations
import datetime as dt, json, os, pathlib, re, sqlite3, stat, subprocess, sys

FORBIDDEN = ("password", "secret", "token", "credential", "api_key", "private_key", "authorization")
ALLOWED_CREDENTIAL_VALUE = {"PASS", "NOT_APPLICABLE"}
REQUIRED_CONFIGURATION = (
    "DRCLOUD_SECRET_KEY", "DRCLOUD_ADMIN_USERNAME", "DRCLOUD_ADMIN_PASSWORD",
    "PRESTASHOP_API_KEY", "PRESTASHOP_PAID_STATE_IDS", "SHOPCAISSE_API_KEY",
    "QONTO_CREDENTIAL", "SUMUP_API_KEY", "SUMUP_MERCHANT_CODE",
)
ACTUAL_SECRET_MATERIAL = (
    "DRCLOUD_SECRET_KEY", "DRCLOUD_ADMIN_PASSWORD", "PRESTASHOP_API_KEY",
    "SHOPCAISSE_API_KEY", "QONTO_CREDENTIAL", "SUMUP_API_KEY",
)

def sanitize_evidence(value):
    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if any(word in str(key).lower() for word in FORBIDDEN):
                    # Only the two explicitly aggregate, non-material fields in
                    # the public contract are allowed.
                    if key not in {"critical_secret_presence", "tracked_git_secret_leak", "credential_verification", "admin_authorization", "password_storage", "plaintext_password_stored"}:
                        raise RuntimeError("EVIDENCE_SANITIZER_REJECTED")
                walk(child)
        elif isinstance(item, list):
            for child in item: walk(child)
        elif isinstance(item, str):
            low=item.lower()
            if re.search(r"bearer\s+\S+|(?:password|secret|token|api[_-]?key|credential)\s*[:=]", low):
                raise RuntimeError("EVIDENCE_SANITIZER_REJECTED")
    walk(value)
    if value.get("bootstrap_admin", {}).get("credential_verification") not in ALLOWED_CREDENTIAL_VALUE:
        raise ValueError("invalid credential verification")
    return value

def fail(code):
    # Stable codes only: never interpolate inspected data.
    raise RuntimeError(code)

def parse_env(path):
    values={}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line=raw.strip()
            if not line or line.startswith("#"): continue
            if "=" not in line: fail("PRODUCTION_ENV_FILE_INVALID")
            key,value=line.split("=",1); values[key]=value
    except (OSError, UnicodeError): fail("PRODUCTION_ENV_FILE_INVALID")
    return values

def hash_policy(encoded):
    try:
        algorithm, rounds, salt, digest=encoded.split("$",3)
        rounds=int(rounds)
        if algorithm != "pbkdf2_sha256" or rounds <= 0 or len(bytes.fromhex(salt)) < 16 or len(bytes.fromhex(digest)) != 32:
            return "INVALID"
        return "CURRENT" if rounds >= 600000 else "LEGACY_VALID"
    except (AttributeError, ValueError, TypeError): return "INVALID"

def inspect_database(db_path, username):
    uri=f"file:{pathlib.Path(db_path).resolve()}?mode=ro"
    try:
        db=sqlite3.connect(uri,uri=True); db.row_factory=sqlite3.Row
        rows=db.execute("""SELECT u.user_id,u.status,c.password_hash,
          EXISTS(SELECT 1 FROM security_user_roles ur WHERE ur.user_id=u.user_id AND ur.role_id='ADMIN') AS admin
          FROM security_users u JOIN local_credentials c ON c.account_id=u.credential_ref
          WHERE u.username=? COLLATE NOCASE""",(username,)).fetchall()
    except sqlite3.Error: fail("BOOTSTRAP_DATABASE_UNAVAILABLE")
    finally:
        if 'db' in locals(): db.close()
    if not rows: fail("BOOTSTRAP_ACCOUNT_MISSING")
    if len(rows) != 1: fail("BOOTSTRAP_ACCOUNT_DUPLICATED")
    row=rows[0]
    if row["status"] != "ACTIVE": fail("BOOTSTRAP_ACCOUNT_INACTIVE")
    if not row["admin"]: fail("BOOTSTRAP_ACCOUNT_NOT_ADMIN")
    policy=hash_policy(row["password_hash"])
    if policy == "LEGACY_VALID": fail("BOOTSTRAP_PASSWORD_HASH_LEGACY_WORK_FACTOR")
    if policy != "CURRENT": fail("BOOTSTRAP_PASSWORD_STORAGE_INVALID")
    return {"account_present":True,"unique":True,"active":True,"admin_authorization":"PROVEN",
            "password_storage":"HASHED","plaintext_password_stored":False,"credential_verification":"NOT_APPLICABLE"}

def command(*args, input_text=None):
    try:
        return subprocess.run(args,input=input_text,text=True,capture_output=True,check=True).stdout
    except (OSError,subprocess.CalledProcessError): fail("PRODUCTION_RUNTIME_CHECK_FAILED")

def secret_values_for_leak_scan(env):
    values=[]
    for key in ACTUAL_SECRET_MATERIAL:
        value=env[key]
        # A classified production secret must have enough entropy-bearing
        # material to scan safely. Fail closed rather than turning a trivial
        # value into an effectively repository-wide substring search.
        if len(value.encode()) < 8 or value.strip().lower() in {"password", "secret", "changeme", "test", "admin"}:
            fail("PRODUCTION_SECRET_MATERIAL_INVALID")
        values.append(value.encode())
    return values

def inspect_database_container(compose):
    program=r'''import json,os,sqlite3
+db=sqlite3.connect("file:/data/drcloud.db?mode=ro",uri=True);db.row_factory=sqlite3.Row
+rows=db.execute("SELECT u.status,c.password_hash,EXISTS(SELECT 1 FROM security_user_roles ur WHERE ur.user_id=u.user_id AND ur.role_id='ADMIN') admin FROM security_users u JOIN local_credentials c ON c.account_id=u.credential_ref WHERE u.username=? COLLATE NOCASE",(os.environ.get("DRCLOUD_ADMIN_USERNAME",""),)).fetchall();db.close();code="PASS"
+if not rows:code="BOOTSTRAP_ACCOUNT_MISSING"
+elif len(rows)!=1:code="BOOTSTRAP_ACCOUNT_DUPLICATED"
+elif rows[0]["status"]!="ACTIVE":code="BOOTSTRAP_ACCOUNT_INACTIVE"
+elif not rows[0]["admin"]:code="BOOTSTRAP_ACCOUNT_NOT_ADMIN"
+else:
+ try:
+  a,r,s,d=rows[0]["password_hash"].split("$",3); r=int(r); valid=a=="pbkdf2_sha256" and r>0 and len(bytes.fromhex(s))>=16 and len(bytes.fromhex(d))==32
+  if valid and r<600000:code="BOOTSTRAP_PASSWORD_HASH_LEGACY_WORK_FACTOR"
+  elif not valid:code="BOOTSTRAP_PASSWORD_STORAGE_INVALID"
+ except Exception:code="BOOTSTRAP_PASSWORD_STORAGE_INVALID"
+print(json.dumps({"code":code}))'''.replace("\n+","\n")
    # A failed Compose invocation retains its orchestration error.  Only a
    # successful exec with an unusable inspection response is classified as a
    # database-inspection failure.
    raw=command(*compose,"exec","-T","drcloud-os","python","-c",program)
    try: code=json.loads(raw)["code"]
    except (json.JSONDecodeError, KeyError, TypeError): fail("BOOTSTRAP_DATABASE_UNAVAILABLE")
    if code!="PASS": fail(code if re.fullmatch(r"[A-Z_]+",code) else "BOOTSTRAP_DATABASE_UNAVAILABLE")
    return {"account_present":True,"unique":True,"active":True,"admin_authorization":"PROVEN","password_storage":"HASHED","plaintext_password_stored":False,"credential_verification":"NOT_APPLICABLE"}

def main():
    if len(sys.argv)==3 and sys.argv[1]=="--sanitize":
        data=json.loads(pathlib.Path(sys.argv[2]).read_text()); sanitize_evidence(data); print(json.dumps(data,separators=(",",":"))); return
    env_path=pathlib.Path(os.environ.get("DRCLOUD_PROOF_ENV_FILE","/opt/drcloud-os/deploy/ovh/drcloud.env"))
    if not env_path.is_file() or env_path.is_symlink(): fail("PRODUCTION_ENV_FILE_INVALID")
    if stat.S_IMODE(env_path.stat().st_mode) & 0o077: fail("PRODUCTION_ENV_PERMISSIONS_INVALID")
    env=parse_env(env_path)
    exact={"DRCLOUD_ENV":"production","DRCLOUD_SAFE_MODE":"true","BARCODE_SYNC_MODE":"dry-run",
           "QONTO_CREDENTIAL_REF":"env:QONTO_CREDENTIAL","QONTO_API_URL":"https://thirdparty.qonto.com",
           "PRESTASHOP_API_URL":"https://dr-cloudshop.com/api","SUMUP_API_URL":"https://api.sumup.com"}
    for key,want in exact.items():
        if env.get(key) != want: fail("PRODUCTION_ENV_FILE_INVALID")
    for key in REQUIRED_CONFIGURATION:
        if not env.get(key): fail("PRODUCTION_CRITICAL_SECRET_MISSING")
        if env[key].strip().upper() in {"CHANGE_ME","NOT_CONFIGURED"}: fail("PRODUCTION_PLACEHOLDER_DETECTED")
    repo=pathlib.Path(os.environ.get("DRCLOUD_PROOF_REPO","/opt/drcloud-os"))
    tracked=command("git","-C",str(repo),"ls-files","-z").split("\0")
    critical=secret_values_for_leak_scan(env)
    for rel in tracked:
        if not rel: continue
        try: content=(repo/rel).read_bytes()
        except OSError: fail("PRODUCTION_SECRET_LEAK_CHECK_FAILED")
        if any(value in content for value in critical): fail("PRODUCTION_SECRET_LEAK_DETECTED")
    if env_path.resolve() in {(repo/p).resolve() for p in tracked if p}:
        fail("PRODUCTION_SECRET_LEAK_DETECTED")
    compose=("docker","compose","-f",str(repo/"deploy/ovh/docker-compose.yml"))
    db_path=os.environ.get("DRCLOUD_PROOF_DATABASE")
    # The environment value seeds a missing credential only.  Once durable
    # storage exists (and can be changed in the UI), it is no longer the
    # authority for the current password; the read-only proof checks each fact
    # independently and never attempts a login or mutation.
    admin=inspect_database(db_path,env["DRCLOUD_ADMIN_USERNAME"]) if db_path else inspect_database_container(compose)
    for service in ("drcloud-os","automation-worker"):
        if not command(*compose,"ps","--quiet","--status","running",service).strip(): fail("PRODUCTION_RUNTIME_CHECK_FAILED")
    health=json.loads(command("curl","--fail","--silent","--show-error","http://127.0.0.1:8080/health"))
    if health.get("status")!="ok" or health.get("database")!="ok": fail("PRODUCTION_RUNTIME_CHECK_FAILED")
    deployed=command("git","-C",str(repo),"rev-parse","HEAD").strip()
    if health.get("commit") != deployed: fail("RUNTIME_COMMIT_MISMATCH")
    evidence={"evidence_level":"PRODUCTION_SECRETS_BOOTSTRAP_PROOF","timestamp":dt.datetime.now(dt.timezone.utc).isoformat(),
      "deployed_commit":deployed,"runtime":{"app_running":True,"worker_running":True,"health":"HEALTH_OK","database":"OK","commit_match":True},
      "environment":{"runtime_env_file":"PROTECTED","production_mode":"PROVEN","critical_secret_presence":"PROVEN","placeholders_absent":True,"tracked_git_secret_leak":False},
      "bootstrap_admin":admin,"result":"PRODUCTION_BOOTSTRAP_PROVEN"}
    sanitize_evidence(evidence)
    output=pathlib.Path(os.environ.get("DRCLOUD_PROOF_OUTPUT","production_bootstrap_evidence.json"))
    output.write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8"); os.chmod(output,0o600)

try: main()
except Exception as exc:
    code=str(exc) if isinstance(exc,RuntimeError) and re.fullmatch(r"[A-Z_]+",str(exc)) else "PRODUCTION_BOOTSTRAP_PROOF_FAILED"
    print(code,file=sys.stderr); raise SystemExit(1)
PY
