import json
from datetime import datetime, timedelta, timezone

import pytest

from dr_cloud_sync.security import AuthorizationService, SecurityStore, sanitise
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
    encoded=json.dumps(store.audits())
    assert "abc" not in encoded and "[REDACTED]" in encoded


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
