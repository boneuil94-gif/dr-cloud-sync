import json
import sqlite3

from dr_cloud_sync.security import ADMIN_ACCOUNT_ID, CredentialStore, verify_password
from test_os_production import configured, login, request  # noqa: F401


NEW_PASSWORD = "a-new-secure-passphrase"


def change(app, cookie, current="very-secret-password", new=NEW_PASSWORD,
           confirmation=NEW_PASSWORD, csrf=None):
    session = app._session({"HTTP_COOKIE": cookie})
    headers = {"X-CSRF-Token": csrf if csrf is not None else session["csrf"]}
    return request(app, "/api/security/change-password", "POST", {
        "current_password": current, "new_password": new,
        "new_password_confirmation": confirmation,
    }, cookie, headers)


def test_security_page_is_authenticated_password_only_and_not_cached(configured):
    app, _ = configured
    assert request(app, "/securite")[0] == "303 See Other"
    _, cookie = login(app)
    status, headers, body = request(app, "/securite", cookie=cookie)
    html = body.decode()
    assert status == "200 OK" and headers["Cache-Control"] == "no-store"
    assert html.count('type="password"') == 3
    assert "Mot de passe actuel" in html and "Changer le mot de passe" in html
    assert 'value="very-secret-password"' not in html


def test_password_change_invalidates_sessions_and_changes_login(configured):
    app, _ = configured
    _, first = login(app); _, second = login(app)
    status, headers, body = change(app, first)
    value = json.loads(body)
    assert status == "200 OK" and value == {"success": True, "reauthentication_required": True}
    assert headers["Cache-Control"] == "no-store" and "Max-Age=0" in headers["Set-Cookie"]
    assert app._session({"HTTP_COOKIE": first}) is None
    assert app._session({"HTTP_COOKIE": second}) is None
    assert login(app)[0] == "401 Unauthorized"
    assert login(app, NEW_PASSWORD)[0] == "303 See Other"
    assert "password_hash" not in body.decode()


def test_password_validation_csrf_and_current_password(configured):
    app, _ = configured; _, cookie = login(app)
    assert change(app, cookie, csrf="wrong")[0] == "403 Forbidden"
    assert change(app, cookie, current="incorrect")[0] == "400 Bad Request"
    assert change(app, cookie, confirmation="different-password")[0] == "400 Bad Request"
    assert change(app, cookie, new="short", confirmation="short")[0] == "400 Bad Request"
    assert change(app, cookie, new="password1234", confirmation="password1234")[0] == "400 Bad Request"
    assert change(app, cookie, new="very-secret-password", confirmation="very-secret-password")[0] == "400 Bad Request"
    assert login(app)[0] == "303 See Other"


def test_migration_persistence_and_secret_free_activity(configured):
    app, settings = configured; credential = app.credentials.get()
    assert credential.account_id == ADMIN_ACCOUNT_ID
    assert credential.password_hash != settings.admin_password
    assert settings.admin_password not in credential.password_hash
    assert verify_password(settings.admin_password, credential.password_hash)
    _, cookie = login(app); assert change(app, cookie)[0] == "200 OK"

    reopened = CredentialStore(settings.database, settings.admin_password)
    assert reopened.verify(NEW_PASSWORD) and not reopened.verify(settings.admin_password)
    row = sqlite3.connect(settings.database).execute(
        "SELECT data FROM activity_logs ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()[0]
    activity = json.loads(row)
    assert activity["event_type"] == "PASSWORD_CHANGED"
    assert activity["metadata"] == {"actor": "admin", "success": True}
    for secret in (settings.admin_password, NEW_PASSWORD, credential.password_hash):
        assert secret not in row


def test_failed_atomic_change_keeps_old_hash(tmp_path):
    store = CredentialStore(tmp_path / "credentials.db", "initial-password-long")
    before = store.get()
    try:
        store.change_password("wrong-password", NEW_PASSWORD, "admin")
    except PermissionError:
        pass
    assert store.get() == before and store.verify("initial-password-long")
