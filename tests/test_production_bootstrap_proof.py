import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

ROOT=Path(__file__).parents[1]
SCRIPT=ROOT/"deploy/ovh/production-bootstrap-proof.sh"
FAKE_PASSWORD="fixture-only-admin-password"

def password_hash(password=FAKE_PASSWORD):
    salt=b"fixture-salt-123"
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"

def fixture(tmp_path, *, env_change=None, mode=0o600, users=None, commit_match=True):
    repo=tmp_path/"repo"; repo.mkdir(); subprocess.run(["git","init","-q",repo],check=True)
    (repo/"safe.txt").write_text("tracked public fixture\n")
    subprocess.run(["git","-C",repo,"add","safe.txt"],check=True)
    subprocess.run(["git","-C",repo,"-c","user.email=t@example.test","-c","user.name=Test","commit","-qm","fixture"],check=True)
    commit=subprocess.check_output(["git","-C",repo,"rev-parse","HEAD"],text=True).strip()
    values={"DRCLOUD_ENV":"production","DRCLOUD_SECRET_KEY":"fixture-app-value","DRCLOUD_ADMIN_USERNAME":"proof-admin",
      "DRCLOUD_ADMIN_PASSWORD":FAKE_PASSWORD,"DRCLOUD_SAFE_MODE":"true","BARCODE_SYNC_MODE":"dry-run",
      "QONTO_CREDENTIAL_REF":"env:QONTO_CREDENTIAL","QONTO_CREDENTIAL":"fixture-qonto-value","QONTO_API_URL":"https://thirdparty.qonto.com",
      "PRESTASHOP_API_URL":"https://dr-cloudshop.com/api","PRESTASHOP_API_KEY":"fixture-presta-value","PRESTASHOP_PAID_STATE_IDS":"2",
      "SHOPCAISSE_API_KEY":"fixture-shop-value","SUMUP_API_URL":"https://api.sumup.com","SUMUP_API_KEY":"fixture-sumup-value","SUMUP_MERCHANT_CODE":"fixture-merchant"}
    if env_change:
        for key,value in env_change.items():
            if value is None: values.pop(key,None)
            else: values[key]=value
    env_file=tmp_path/"runtime.env"; env_file.write_text("".join(f"{k}={v}\n" for k,v in values.items())); env_file.chmod(mode)
    db=tmp_path/"db.sqlite"; con=sqlite3.connect(db)
    con.executescript("CREATE TABLE security_users(user_id TEXT,username TEXT,status TEXT,credential_ref TEXT); CREATE TABLE local_credentials(account_id TEXT,password_hash TEXT); CREATE TABLE security_user_roles(user_id TEXT,role_id TEXT);")
    if users is None: users=[("u","proof-admin","ACTIVE","c",password_hash(),True)]
    for uid,name,status,cred,hashed,admin in users:
        con.execute("INSERT INTO security_users VALUES(?,?,?,?)",(uid,name,status,cred)); con.execute("INSERT INTO local_credentials VALUES(?,?)",(cred,hashed))
        if admin: con.execute("INSERT INTO security_user_roles VALUES(?,?)",(uid,"ADMIN"))
    con.commit(); con.close()
    bindir=tmp_path/"bin"; bindir.mkdir()
    (bindir/"docker").write_text("#!/bin/sh\nprintf 'running-container-id\\n'\n"); (bindir/"docker").chmod(0o755)
    health_commit=commit if commit_match else "0"*40
    (bindir/"curl").write_text(f"#!/bin/sh\nprintf '%s\\n' '{{\"status\":\"ok\",\"database\":\"ok\",\"commit\":\"{health_commit}\"}}'\n"); (bindir/"curl").chmod(0o755)
    output=tmp_path/"evidence.json"
    env={**os.environ,"PATH":f"{bindir}:{os.environ['PATH']}","DRCLOUD_PROOF_ENV_FILE":str(env_file),"DRCLOUD_PROOF_REPO":str(repo),"DRCLOUD_PROOF_DATABASE":str(db),"DRCLOUD_PROOF_OUTPUT":str(output)}
    return subprocess.run([SCRIPT],env=env,text=True,capture_output=True), output, repo, values

def test_valid_environment_and_hash_produce_safe_evidence(tmp_path):
    result,out,_,_=fixture(tmp_path)
    assert result.returncode==0 and result.stdout=="" and json.loads(out.read_text())["result"]=="PRODUCTION_BOOTSTRAP_PROVEN"

@pytest.mark.parametrize(("change","code"),[
    ({"DRCLOUD_SECRET_KEY":None},"PRODUCTION_CRITICAL_SECRET_MISSING"),
    ({"DRCLOUD_SECRET_KEY":""},"PRODUCTION_CRITICAL_SECRET_MISSING"),
    ({"DRCLOUD_SECRET_KEY":"CHANGE_ME"},"PRODUCTION_PLACEHOLDER_DETECTED"),
])
def test_invalid_secret_fails_without_displaying_value(tmp_path,change,code):
    result,_,_,_=fixture(tmp_path,env_change=change)
    assert result.returncode==1 and result.stderr.strip()==code and "fixture-" not in result.stderr

def test_permissions_too_broad(tmp_path):
    result,_,_,_=fixture(tmp_path,mode=0o640)
    assert result.stderr.strip()=="PRODUCTION_ENV_PERMISSIONS_INVALID"

@pytest.mark.parametrize(("users","code"),[
    ([],"BOOTSTRAP_ACCOUNT_MISSING"),
    ([('a','proof-admin','ACTIVE','a',password_hash(),True),('b','proof-admin','ACTIVE','b',password_hash(),True)],"BOOTSTRAP_ACCOUNT_DUPLICATED"),
    ([('a','proof-admin','ACTIVE','a',password_hash(),False)],"BOOTSTRAP_ACCOUNT_NOT_ADMIN"),
    ([('a','proof-admin','ACTIVE','a',FAKE_PASSWORD,True)],"BOOTSTRAP_PASSWORD_STORAGE_INVALID"),
])
def test_invalid_admin_states(tmp_path,users,code):
    result,_,_,_=fixture(tmp_path,users=users)
    assert result.returncode==1 and result.stderr.strip()==code and FAKE_PASSWORD not in result.stderr

def test_runtime_commit_mismatch(tmp_path):
    result,_,_,_=fixture(tmp_path,commit_match=False)
    assert result.stderr.strip()=="RUNTIME_COMMIT_MISMATCH"

@pytest.mark.parametrize("document",[
    {"bootstrap_admin":{"password":"x","credential_verification":"PASS"}},
    {"bootstrap_admin":{"credential_verification":"PASS"},"message":"Bearer fixture-token"},
])
def test_sanitizer_blocks_sensitive_key_or_value(tmp_path,document):
    path=tmp_path/"bad.json"; path.write_text(json.dumps(document))
    result=subprocess.run([SCRIPT,"--sanitize",path],text=True,capture_output=True)
    assert result.returncode==1 and result.stderr.strip()=="EVIDENCE_SANITIZER_REJECTED" and "fixture-token" not in result.stderr

def test_sanitizer_accepts_generated_evidence(tmp_path):
    result,out,_,_=fixture(tmp_path); assert result.returncode==0
    checked=subprocess.run([SCRIPT,"--sanitize",out],text=True,capture_output=True)
    assert checked.returncode==0 and json.loads(checked.stdout)["result"]=="PRODUCTION_BOOTSTRAP_PROVEN"

def rerun_fixture(tmp_path, repo):
    # Re-run using the original fixture environment assembled from its paths.
    env={**os.environ,"DRCLOUD_PROOF_ENV_FILE":str(tmp_path/"runtime.env"),"DRCLOUD_PROOF_REPO":str(repo),"DRCLOUD_PROOF_DATABASE":str(tmp_path/"db.sqlite"),"DRCLOUD_PROOF_OUTPUT":str(tmp_path/"x.json"),"PATH":f"{tmp_path/'bin'}:{os.environ['PATH']}"}
    return subprocess.run([SCRIPT],env=env,text=True,capture_output=True)

@pytest.mark.parametrize("key",[
    "PRESTASHOP_PAID_STATE_IDS",
    "DRCLOUD_ADMIN_USERNAME",
    "SUMUP_MERCHANT_CODE",
])
def test_tracked_non_secret_configuration_is_not_treated_as_a_leak(tmp_path,key):
    _,_,repo,values=fixture(tmp_path)
    (repo/"safe.txt").write_text(values[key])
    result=rerun_fixture(tmp_path,repo)
    assert result.returncode==0 and result.stderr=="" and result.stdout==""

@pytest.mark.parametrize("key",[
    "QONTO_CREDENTIAL",
    "DRCLOUD_ADMIN_PASSWORD",
    "DRCLOUD_SECRET_KEY",
])
def test_tracked_secret_value_is_rejected_without_echo(tmp_path,key):
    _,_,repo,values=fixture(tmp_path)
    (repo/"safe.txt").write_text(values[key])
    result=rerun_fixture(tmp_path,repo)
    assert result.returncode==1
    assert result.stderr.strip()=="PRODUCTION_SECRET_LEAK_DETECTED"
    assert result.stdout==""
    assert all(value not in result.stderr for value in values.values())

def test_trivial_classified_secret_fails_closed_without_scanning_or_echo(tmp_path):
    result,_,_,values=fixture(tmp_path,env_change={"QONTO_CREDENTIAL":"test"})
    assert result.returncode==1
    assert result.stderr.strip()=="PRODUCTION_SECRET_MATERIAL_INVALID"
    assert result.stdout==""
    assert all(value not in result.stderr for value in values.values())
