import json
import subprocess
import time
import pytest
from io import BytesIO
from PIL import Image
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dr_cloud_sync.admin_status import AdminStatusService
from dr_cloud_sync.modules import render_navigation
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
    assert set(json.loads(body)) == {"status", "checked_at", "application", "database", "deployment", "backup", "system", "media", "prestashop"}
    assert request(app, "/api/admin/catalogue-rehydration/status")[0] == "303 See Other"
    assert request(app, "/api/admin/shopcaisse-sales/failures")[0] == "303 See Other"
    assert request(app, "/api/admin/sumup-schema")[0] == "303 See Other"


def test_administration_exposes_non_sensitive_sumup_schema_diagnostic(configured):
    app, _ = configured
    _, cookie = login(app)
    status, _, body = request(app, "/api/admin/sumup-schema", cookie=cookie)
    payload = json.loads(body)
    assert status == "200 OK"
    assert payload["schema_version"] == payload["target_version"] == 1
    assert payload["pending_migrations"] == []
    assert payload["last_check"]["result"] == "OK"
    assert set(payload) == {"schema_version", "target_version", "applied_migrations",
                            "pending_migrations", "last_check", "added_columns_this_start"}
    assert "raw_json" not in body.decode() and "SUMUP_API_KEY" not in body.decode()


def test_administration_exposes_shopcaisse_failures_read_only(configured):
    app, _ = configured
    report={"invalid":1,"failure_details":[{"sale":"SC-3","sold_at":"2026-08-01T09:30:00Z",
        "amount":"18.90","currency":"EUR","store":"Lyon","stage":"INGESTION_LINE",
        "category":"VALIDATION","message":"Donnée invalide","retryable":False,"permanent":True}]}
    with app.sales.db:
        app.sales.db.execute("INSERT INTO sales_sync_states(source,status,last_report_json) VALUES('SHOPCAISSE','SUCCESS',?) ON CONFLICT(source) DO UPDATE SET last_report_json=excluded.last_report_json",(json.dumps(report),))
    _, cookie=login(app)
    status, _, body=request(app,"/api/admin/shopcaisse-sales/failures",cookie=cookie)
    payload=json.loads(body)
    assert status=="200 OK" and payload["count"]==1
    assert payload["failures"][0]["shopcaisse_id"]=="SC-3"
    assert payload["failures"][0]["stage"]=="INGESTION_LINE"
    html=(ROOT/"src/dr_cloud_sync/static/administration.html").read_text()
    assert "Voir les ventes en échec" in html and 'id="failedSalesDialog"' in html


def _wait_job(app, cookie):
    for _ in range(100):
        status = json.loads(request(app, "/api/admin/catalogue-rehydration/status", cookie=cookie)[2])
        if status["state"] not in {"PENDING", "RUNNING"}:
            return status
        time.sleep(.02)
    raise AssertionError("rehydration job did not finish")


def test_catalogue_rehydration_admin_preview_report_apply_and_csrf(configured, tmp_path):
    app, settings = configured
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"catalogue": [{"id": str(i), "nom": f"Produit {i}",
        "reference": f"REF-{i}"} for i in range(478)]}))
    from dr_cloud_sync.admin_rehydration import AdminCatalogueRehydration
    app.catalogue_rehydration = AdminCatalogueRehydration(settings.database, snapshot,
        tmp_path / "backups", environment="test", safe_mode=True)
    pictured = app.os_repository.all()[0]
    image = BytesIO(); Image.new("RGB", (24, 24), "peachpuff").save(image, "PNG")
    app.media.add(pictured.drcloud_product_key, image.getvalue(), filename="primary.png")
    _, cookie = login(app); csrf = app._session({"HTTP_COOKIE": cookie})["csrf"]
    assert request(app, "/api/admin/catalogue-rehydration/preview", "POST", {}, cookie)[0] == "403 Forbidden"
    status = request(app, "/api/admin/catalogue-rehydration/preview", "POST", {}, cookie,
                     {"X-CSRF-Token": csrf})[0]
    assert status == "202 Accepted"
    completed = _wait_job(app, cookie)
    assert completed["state"] == "SUCCEEDED"
    report_id = completed["last_preview"]["metrics"]["report_id"]
    report = json.loads(request(app,
        f"/api/admin/catalogue-rehydration/report?report_id={report_id}&page=1&per_page=25&classification=SAFE&search=Produit",
        cookie=cookie)[2])
    assert report["summary"]["processed"] == 478 and len(report["items"]) == 25
    catalogue_before = json.loads(request(app, "/api/catalogue", cookie=cookie)[2])
    assert [item["canonical"]["drcloud_product_key"] for item in report["items"]] == [
        item["drcloud_product_key"] for item in catalogue_before[:25]]
    canonical = report["items"][0]["canonical"]
    assert canonical["display_name"] == catalogue_before[0]["display_name"]
    assert canonical["base_name"] == catalogue_before[0]["base_name"]
    assert canonical["variant_name"] == catalogue_before[0]["variant_name"]
    assert canonical["attributes"] == catalogue_before[0]["attributes"]
    assert canonical["ean"] == "" and canonical["reference"] == ""
    assert canonical["primary_media"]["role"] == "PRIMARY"
    assert canonical["primary_media"]["thumbnail_url"].startswith("/media/")
    assert report["items"][1]["canonical"]["primary_media"] is None
    # Historical NO_DATA remains available, but only alongside canonical values.
    assert report["items"][0]["fields"]["ean"] == "NO_DATA"
    body = {"report_id": report_id}
    assert request(app, "/api/admin/catalogue-rehydration/apply", "POST", body, cookie,
                   {"X-CSRF-Token": csrf})[0] == "202 Accepted"
    applied = _wait_job(app, cookie)
    assert applied["last_apply"]["status"] == "SUCCEEDED"
    assert applied["last_apply"]["metrics"]["backup"].startswith("drcloud-os-backup-")
    # The long-lived web repository was created before the worker committed.
    # The API must nevertheless expose the canonical row, not that stale snapshot.
    catalogue = json.loads(request(app, "/api/catalogue", cookie=cookie)[2])
    assert next(row for row in catalogue if str(row["product_id"]) == "0")["reference"] == "REF-0"
    duplicate = json.loads(request(app, "/api/admin/catalogue-rehydration/apply", "POST", body, cookie,
                   {"X-CSRF-Token": csrf})[2])
    assert duplicate["reused"] is True


def test_catalogue_contract_does_not_diagnose_simple_products_as_unknown_variants(configured):
    app, _ = configured
    simple = app.os_repository.all()[0]
    with app.os_repository.db:
        app.os_repository.db.execute(
            "UPDATE drcloud_products SET combination_id=NULL WHERE drcloud_product_key=?",
            (simple.drcloud_product_key,),
        )
    _, cookie = login(app)
    rows = json.loads(request(app, "/api/catalogue", cookie=cookie)[2])
    row = next(item for item in rows if item["drcloud_product_key"] == simple.drcloud_product_key)
    assert {"base_name", "variant_name", "display_name", "attributes", "reference", "ean",
            "primary_media", "diagnostics"} <= row.keys()
    assert row["display_name"] == row["base_name"]
    assert "Variante inconnue" not in row["diagnostics"]
    unknown = json.loads(request(app, "/api/catalogue?filter=UNKNOWN_VARIANT", cookie=cookie)[2])
    assert simple.drcloud_product_key not in {item["drcloud_product_key"] for item in unknown}
    quality = json.loads(request(app, "/api/catalogue/quality", cookie=cookie)[2])
    assert quality["missing_variant"] == 477


def test_catalogue_rehydration_rejects_missing_and_stale_preview(configured, tmp_path):
    app, settings = configured
    snapshot = tmp_path / "snapshot.json"; snapshot.write_text('{"catalogue":[{"id":"0","nom":"Produit 0"}]}')
    from dr_cloud_sync.admin_rehydration import AdminCatalogueRehydration
    app.catalogue_rehydration = AdminCatalogueRehydration(settings.database, snapshot,
        tmp_path / "backups", environment="test", safe_mode=True)
    _, cookie = login(app); csrf = app._session({"HTTP_COOKIE": cookie})["csrf"]
    headers = {"X-CSRF-Token": csrf}
    assert request(app, "/api/admin/catalogue-rehydration/apply", "POST",
                   {"report_id": "missing"}, cookie, headers)[0] == "409 Conflict"
    request(app, "/api/admin/catalogue-rehydration/preview", "POST", {}, cookie, headers)
    report_id = _wait_job(app, cookie)["last_preview"]["metrics"]["report_id"]
    product = app.os_repository.all()[0]
    app.os_repository.apply_commercial_changes(product.drcloud_product_key,
        {"base_name": ("Modification manuelle", "MANUAL")}, [])
    status, _, body = request(app, "/api/admin/catalogue-rehydration/apply", "POST",
                              {"report_id": report_id}, cookie, headers)
    assert status == "409 Conflict" and "obsolète" in body.decode()


def test_healthy_application_database_metadata_and_no_secrets(configured, monkeypatch, tmp_path):
    app, settings = configured
    marker = tmp_path / ".last-successful-commit"; marker.write_text("a" * 40)
    backups = tmp_path / "backups"; backup = backups / "backup-1"; backup.mkdir(parents=True); (backup / "drcloud.db").write_bytes(b"db")
    (backup / "metadata.json").write_text(json.dumps({"created_at": "20260729T110000Z", "media":{"included":True,"files":[]}}))
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
    navigation = render_navigation("administration")
    assert 'class="dc-sidebar"' in shell
    assert 'href="/administration" aria-current="page"' in navigation
    assert 'href="/"' in navigation and "Tableau de bord" in navigation
    for section in ("application", "database", "backup", "deployment", "system"):
        assert f'data-section="{section}"' in html
    for status in ("status-ok", "status-warning", "status-error", "status-unknown"):
        assert status in css
    for token in ("--dc-green", "--dc-sidebar", "--dc-background", "--dc-surface",
                  "--dc-success", "--dc-warning", "--dc-danger", "--dc-info"):
        assert token in css


def test_rehydration_source_health_and_operator_safe_missing_source(configured, tmp_path):
    from dr_cloud_sync.admin_rehydration import AdminCatalogueRehydration
    app, settings = configured
    app.catalogue_rehydration = AdminCatalogueRehydration(
        settings.database, tmp_path / "absent.json", tmp_path / "backups",
        environment="test", safe_mode=True)
    _, cookie = login(app); csrf = app._session({"HTTP_COOKIE": cookie})["csrf"]
    health = json.loads(request(app, "/api/admin/catalogue-rehydration/status", cookie=cookie)[2])
    assert health["sources"]["catalogue_local"]["available"] is True
    assert health["sources"]["mapping_historique"] == {
        "status": "UNAVAILABLE", "available": False,
        "source": "PACKAGED_HISTORICAL_MAPPING"}
    assert health["sources"]["prestashop"]["status"] == "NOT_USED"
    request(app, "/api/admin/catalogue-rehydration/preview", "POST", {}, cookie,
            {"X-CSRF-Token": csrf})
    completed = _wait_job(app, cookie)
    assert completed["state"] == "FAILED"
    error = completed["current_job"]["error"]
    assert error.startswith("Analyse impossible :")
    assert "HistoricalCatalogueUnavailable" not in error and str(tmp_path) not in error
    assert completed["last_apply"] is None


def test_default_packaged_preview_is_production_like_and_read_only(configured):
    app, _ = configured
    _, cookie = login(app); csrf = app._session({"HTTP_COOKIE": cookie})["csrf"]
    before_products = [vars(product).copy() for product in app.os_repository.all()]
    with app.service.repo.db as db:
        before_movements = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    request(app, "/api/admin/catalogue-rehydration/preview", "POST", {}, cookie,
            {"X-CSRF-Token": csrf})
    completed = _wait_job(app, cookie)
    assert completed["state"] == "SUCCEEDED"
    assert completed["last_preview"]["metrics"]["processed"] == 478
    assert [vars(product).copy() for product in app.os_repository.all()] == before_products
    with app.service.repo.db as db:
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == before_movements
    assert completed["last_apply"] is None
