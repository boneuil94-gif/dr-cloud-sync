import json
import subprocess
import pytest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dr_cloud_sync.admin_status import AdminStatusService
from test_os_production import configured, login, request

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)


def test_administration_page_and_api_require_authentication(configured):
    app, _ = configured
    assert request(app, "/administration")[0] == "303 See Other"
    assert request(app, "/api/admin/status")[0] == "303 See Other"
    _, cookie = login(app)
    status, _, html = request(app, "/administration", cookie=cookie)
    assert status == "200 OK" and b'id="adminHero"' in html
    status, _, body = request(app, "/api/admin/status", cookie=cookie)
    assert status == "200 OK"
    assert set(json.loads(body)) == {"status", "checked_at", "application", "database", "deployment", "backup", "system"}


def test_healthy_application_database_metadata_and_no_secrets(configured, monkeypatch, tmp_path):
    app, settings = configured
    marker = tmp_path / ".last-successful-commit"; marker.write_text("a" * 40)
    backups = tmp_path / "backups"; backup = backups / "backup-1"; backup.mkdir(parents=True); (backup / "drcloud.db").write_bytes(b"db")
    (backup / "metadata.json").write_text(json.dumps({"created_at": "20260729T110000Z"}))
    monkeypatch.setenv("DRCLOUD_BUILD_COMMIT", "a" * 40); monkeypatch.setenv("DRCLOUD_BUILD_DATE", "2026-07-29T10:00:00Z")
    app.admin_status = AdminStatusService(settings.database, backup_root=backups, deployment_marker=marker, now=lambda: NOW)
    _, cookie = login(app); payload = json.loads(request(app, "/api/admin/status", cookie=cookie)[2])
    assert payload["application"] == {"status":"ok", "version":"1.0.0", "commit":"a" * 40, "build_date":"2026-07-29T10:00:00Z"}
    assert payload["database"]["status"] == "ok" and payload["database"]["available"] is True and payload["database"]["check"] == "ok"
    assert payload["backup"]["status"] == "ok" and payload["backup"]["count"] == 1 and payload["backup"]["age_seconds"] == 3600
    assert payload["deployment"]["consistency"] == "match"
    serialized = json.dumps(payload).lower()
    for secret in (settings.admin_password, settings.secret_key, "cookie", "token", "session", "api_key", "ssh"):
        assert secret.lower() not in serialized


def test_missing_database_backup_and_build_metadata_are_tolerated(tmp_path, monkeypatch):
    monkeypatch.delenv("DRCLOUD_BUILD_COMMIT", raising=False); monkeypatch.delenv("DRCLOUD_BUILD_DATE", raising=False)
    backups = tmp_path / "backups"; backups.mkdir()
    payload = AdminStatusService(tmp_path / "missing.db", backup_root=backups,
                                 deployment_marker=tmp_path / "missing-marker", now=lambda: NOW).collect()
    assert payload["database"] == {"status":"error", "available":False, "size_bytes":None, "check":"unavailable"}
    assert payload["backup"]["status"] == "warning" and payload["backup"]["count"] == 0
    assert payload["application"]["commit"] == payload["application"]["build_date"] == "unknown"
    assert payload["deployment"]["status"] == "unknown" and payload["status"] == "error"


def test_deployment_mismatch_is_a_controlled_warning(configured, monkeypatch, tmp_path):
    _, settings = configured
    marker = tmp_path / "last-successful-commit"
    marker.write_text("b" * 40 + "\n", encoding="utf-8")
    monkeypatch.setenv("DRCLOUD_BUILD_COMMIT", "a" * 40)
    deployment = AdminStatusService(
        settings.database, backup_root=tmp_path, deployment_marker=marker
    ).collect()["deployment"]
    assert deployment == {
        "status": "warning", "served_commit": "a" * 40,
        "last_successful_commit": "b" * 40, "consistency": "mismatch",
        "build_date": "unknown", "runtime": "application",
    }


@pytest.mark.parametrize("contents", ["", "not-a-sha", "a" * 39, "a" * 41, "../secret"])
def test_empty_or_invalid_deployment_marker_is_unknown(
        configured, monkeypatch, tmp_path, contents):
    _, settings = configured
    marker = tmp_path / "last-successful-commit"
    marker.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("DRCLOUD_BUILD_COMMIT", "a" * 40)
    deployment = AdminStatusService(
        settings.database, backup_root=tmp_path, deployment_marker=marker
    ).collect()["deployment"]
    assert deployment["status"] == "unknown"
    assert deployment["last_successful_commit"] == "unknown"
    assert deployment["consistency"] == "unknown"


def test_invalid_served_commit_cannot_create_false_consistency(configured, monkeypatch, tmp_path):
    _, settings = configured
    marker = tmp_path / "last-successful-commit"
    marker.write_text("unknown", encoding="utf-8")
    monkeypatch.setenv("DRCLOUD_BUILD_COMMIT", "unknown")
    deployment = AdminStatusService(
        settings.database, backup_root=tmp_path, deployment_marker=marker
    ).collect()["deployment"]
    assert deployment["status"] == deployment["consistency"] == "unknown"


def test_invalid_backup_metadata_uses_file_timestamp(configured, tmp_path):
    app, settings = configured; root = tmp_path / "backups"; item = root / "partial"; item.mkdir(parents=True)
    database = item / "drcloud.db"; database.write_bytes(b"db"); (item / "metadata.json").write_text("{invalid")
    timestamp = (NOW - timedelta(days=3)).timestamp()
    import os
    os.utime(database, (timestamp, timestamp))
    payload = AdminStatusService(settings.database, backup_root=root, now=lambda: NOW).collect()
    assert payload["backup"]["status"] == "error" and payload["backup"]["age_seconds"] == 259200


def test_disk_values_are_normalized(configured, tmp_path):
    _, settings = configured; Usage = namedtuple("Usage", "total used free")
    normal = AdminStatusService(settings.database, backup_root=tmp_path, disk_usage=lambda _: Usage(100, 120, 0)).collect()["system"]
    invalid = AdminStatusService(settings.database, backup_root=tmp_path, disk_usage=lambda _: Usage(0, -1, -1)).collect()["system"]
    assert normal["disk"]["used_percent"] == 100 and normal["status"] == "error"
    assert invalid["status"] == "unknown" and invalid["disk"]["used_percent"] is None


def test_health_remains_public_and_minimal(configured):
    app, _ = configured; status, _, body = request(app, "/health"); payload = json.loads(body)
    assert status == "200 OK"
    assert set(payload) == {"status", "application", "version", "commit", "build_date", "database"}
    assert not {"backup", "deployment", "system", "path", "size_bytes"} & set(payload)


def test_administration_javascript_normalizes_partial_and_invalid_values():
    script = r"""
const a = require('./src/dr_cloud_sync/static/administration.js');
if (a.adminModel({status:'bad'}).status !== 'unknown') process.exit(1);
if (a.cleanStatus('warning') !== 'warning' || a.cleanStatus('error') !== 'error' || a.cleanStatus('ok') !== 'ok') process.exit(2);
if (a.clampAdminPercent(140) !== 100 || a.clampAdminPercent(-1) !== null || a.clampAdminPercent('x') !== null) process.exit(3);
if (a.shortSha('<img onerror=alert(1)>') !== 'Inconnu' || a.shortSha('abcdef012345') !== 'abcdef01') process.exit(4);
if (a.formatBytes(null) !== 'Inconnu' || a.adminModel({database:null}).database === null) process.exit(5);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_administration_html_contains_no_operational_values_or_unsafe_sink():
    html = (ROOT / "src/dr_cloud_sync/static/administration.html").read_text()
    script = (ROOT / "src/dr_cloud_sync/static/administration.js").read_text()
    assert "DRCLOUD_BUILD_COMMIT" not in html and "1.0.0" not in html
    assert "innerHTML" not in script and "textContent" in script and 'fetch("/api/admin/status"' in script


def test_administration_uses_drcloud_shell_and_keeps_all_status_fields():
    html = (ROOT / "src/dr_cloud_sync/static/administration.html").read_text()
    shell = (ROOT / "src/dr_cloud_sync/static/app-shell.html").read_text()
    css = (ROOT / "src/dr_cloud_sync/static/inventory.css").read_text()
    assert 'class="dc-sidebar"' in shell
    assert 'href="/administration"{{ACTIVE_administration}}' in shell
    assert 'href="/"' in shell and "Tableau de bord" in shell
    for section in ("application", "database", "backup", "deployment", "system"):
        assert f'data-section="{section}"' in html
    for status in ("status-ok", "status-warning", "status-error", "status-unknown"):
        assert status in css
    for token in ("--dc-green", "--dc-sidebar", "--dc-background", "--dc-surface",
                  "--dc-success", "--dc-warning", "--dc-danger", "--dc-info"):
        assert token in css
