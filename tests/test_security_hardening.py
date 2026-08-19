from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from dr_cloud_sync.security import (
    AuthorizationService, PBKDF2_ITERATIONS, PasswordHashPolicy, SecurityStore,
    password_hash_policy, sanitise, verify_password,
)
from dr_cloud_sync.prestashop import PrestaShopClient, PrestaShopError
from dr_cloud_sync.qonto import EnvironmentSecretProvider
from test_os_production import configured, login, request  # noqa: F401


def test_rbac_default_deny_and_finance_separation(tmp_path):
    store=SecurityStore(tmp_path/"security.db","root","correct-horse-battery-staple")
    staff=store.create_user("clerk","Boutique","unique temporary phrase 739!",["STAFF"],"local-admin")
    auth=AuthorizationService(store)
    auth.require(staff["user_id"],"sales.read")
    with pytest.raises(PermissionError): auth.require(staff["user_id"],"finance.read")
    with pytest.raises(PermissionError): auth.require(staff["user_id"],"security.manage_users")
    with pytest.raises(PermissionError): auth.require(staff["user_id"],"unknown.permission")


def test_disable_role_and_password_changes_revoke_sessions(tmp_path):
    store=SecurityStore(tmp_path/"security.db","root","correct-horse-battery-staple")
    user=store.create_user("operator","Opérateur","temporary secure phrase 851!",["STAFF"],"local-admin")
    credential=store.credential(user["user_id"]); expires=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    sid=store.create_session(user["user_id"],expires,"127.0.0.1")
    assert store.valid_session(sid,user["user_id"],credential.session_version)
    store.assign_roles(user["user_id"],["READ_ONLY"],"local-admin")
    assert not store.valid_session(sid,user["user_id"],credential.session_version)
    sid=store.create_session(user["user_id"],expires,"127.0.0.1"); version=store.credential(user["user_id"]).session_version
    store.set_status(user["user_id"],"DISABLED","local-admin")
    assert not store.valid_session(sid,user["user_id"],version)


def test_recursive_redaction_and_append_only_audit(tmp_path):
    store=SecurityStore(tmp_path/"security.db","root","correct-horse-battery-staple")
    raw={"Authorization":"Bearer abc","nested":{"api_key":"abc","safe":"ok"},"message":"token=abc"}
    clean=sanitise(raw)
    assert clean=={"Authorization":"[REDACTED]","nested":{"api_key":"[REDACTED]","safe":"ok"},"message":"token=[REDACTED]"}
    store.audit("local-admin","SECRET_REF_CHANGED","SECRET","qonto",metadata=raw)
    metadata=store.audits()[0]["metadata"]
    assert metadata["Authorization"]=="[REDACTED]"
    assert metadata["nested"]["api_key"]=="[REDACTED]"
    assert metadata["message"]=="token=[REDACTED]"
    with pytest.raises(Exception): store.db.execute("UPDATE audit_logs SET action='ERASED'")
    with pytest.raises(Exception): store.db.execute("DELETE FROM audit_logs")


def test_system_setting_registry_validation_persistence_and_audit(tmp_path):
    path=tmp_path/"security.db"; store=SecurityStore(path,"root","correct-horse-battery-staple")
    changed=store.set_setting("stock.low_threshold",8,"local-admin",request_id="req-1")
    assert changed["value"]==8 and changed["value_type"]=="int"
    with pytest.raises(KeyError): store.set_setting("unknown",1,"local-admin")
    with pytest.raises(ValueError): store.set_setting("stock.low_threshold","8","local-admin")
    with pytest.raises(ValueError): store._setting_value(type("D",(),{"key":"banner","value_type":"string","choices":()})(),"Bearer private")
    reopened=SecurityStore(path,"root","ignored-bootstrap-password")
    assert next(x for x in reopened.settings() if x["key"]=="stock.low_threshold")["value"]==8
    assert reopened.audits(action="SYSTEM_SETTING_CHANGED")[0]["request_id"]=="req-1"


def test_opaque_secret_resolution_and_prestashop_connector():
    provider=EnvironmentSecretProvider({"PS_RUNTIME":"fake-value"},{"prestashop.production":"PS_RUNTIME"})
    client=PrestaShopClient.from_secret_ref("https://example.test/api","prestashop.production",provider)
    assert "fake-value" not in repr(client.__dict__)
    with pytest.raises(PrestaShopError): PrestaShopClient.from_secret_ref("https://example.test/api","missing.production",provider)


def test_security_headers_and_unknown_authenticated_route_is_denied(configured):
    app,_=configured; _,cookie=login(app)
    status,headers,_=request(app,"/securite",cookie=cookie)
    assert status=="200 OK"
    for header in ("Content-Security-Policy","X-Content-Type-Options","Referrer-Policy","Permissions-Policy","X-Frame-Options"):
        assert header in headers
    assert request(app,"/api/new-sensitive-route",cookie=cookie)[0]=="403 Forbidden"


def test_all_declared_pages_and_api_domains_have_explicit_permissions():
    from dr_cloud_sync.inventory_web import InventoryApp
    assert InventoryApp._route_permission("/finance","GET")=="finance.read"
    assert InventoryApp._route_permission("/api/finance","GET")=="finance.read"
    assert InventoryApp._route_permission("/api/sales/import/apply","POST")=="sales.sync"
    assert InventoryApp._route_permission("/api/security/users/x/status","POST")=="security.manage_users"
    assert InventoryApp._route_permission("/api/not-declared","GET")=="__default_deny__"


def legacy_hash(password, salt=b"legacy-salt-1234", rounds=200_000):
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


@pytest.mark.parametrize(("encoded","policy"),[
    (legacy_hash("fixture password"),PasswordHashPolicy.LEGACY_VALID),
    ("cleartext password",PasswordHashPolicy.INVALID),
    ("pbkdf2_sha256$broken",PasswordHashPolicy.INVALID),
    ("argon2$200000$00$00",PasswordHashPolicy.INVALID),
    ("pbkdf2_sha256$0$6c65676163792d73616c742d31323334$"+"00"*32,PasswordHashPolicy.INVALID),
])
def test_password_hash_policy_fails_closed(encoded,policy):
    assert password_hash_policy(encoded) is policy


def test_verified_legacy_hash_is_upgraded_once_without_identity_mutation(tmp_path):
    path=tmp_path/"pre-pr-86.db"
    store=SecurityStore(path,"root","correct-horse-battery-staple")
    user=store.user("local-admin"); before=store.credential("local-admin")
    old_hash=legacy_hash("historic secure password")
    store.db.execute("UPDATE local_credentials SET password_hash=? WHERE account_id=?",(old_hash,before.account_id)); store.db.commit()
    roles=store.roles_for("local-admin")

    reopened=SecurityStore(path,"ignored-seed","different bootstrap value")
    assert reopened.authenticate("root","historic secure password")==user
    upgraded=reopened.credential("local-admin")
    assert upgraded.password_hash!=old_hash
    assert upgraded.password_hash.split("$")[2]!=old_hash.split("$")[2]
    assert int(upgraded.password_hash.split("$")[1])>=PBKDF2_ITERATIONS
    assert verify_password("historic secure password",upgraded.password_hash)
    assert upgraded.password_changed_at==before.password_changed_at
    assert upgraded.session_version==before.session_version
    assert reopened.user("local-admin")==user
    assert reopened.roles_for("local-admin")==roles

    first_upgraded_hash=upgraded.password_hash
    assert reopened.authenticate("root","historic secure password")
    assert reopened.credential("local-admin").password_hash==first_upgraded_hash
    audits=reopened.audits(action="PASSWORD_HASH_UPGRADED")
    assert len(audits)==1
    assert audits[0]["metadata"]=={"from_policy":"LEGACY_PBKDF2","to_policy":"CURRENT_PBKDF2"}
    serialized=json.dumps(audits)
    for sensitive in ("historic secure password",old_hash,old_hash.split("$")[2],old_hash.split("$")[3]):
        assert sensitive not in serialized


def test_failed_or_invalid_authentication_never_rewrites_hash(tmp_path):
    store=SecurityStore(tmp_path/"security.db","root","correct-horse-battery-staple")
    for encoded in (legacy_hash("right historic password"),"plaintext", "unknown$1$00$00"):
        store.db.execute("UPDATE local_credentials SET password_hash=? WHERE account_id='local-admin'",(encoded,)); store.db.commit()
        assert store.authenticate("root","wrong password") is None
        assert store.credential("local-admin").password_hash==encoded
    assert store.audits(action="PASSWORD_HASH_UPGRADED")==[]


def test_current_hash_authentication_does_not_mutate_credential(tmp_path):
    store=SecurityStore(tmp_path/"security.db","root","correct-horse-battery-staple")
    before=store.credential("local-admin")
    assert store.authenticate("root","correct-horse-battery-staple")
    assert store.credential("local-admin")==before
    assert password_hash_policy(before.password_hash) is PasswordHashPolicy.CURRENT
