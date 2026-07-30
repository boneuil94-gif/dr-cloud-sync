import json
from pathlib import Path

from dr_cloud_sync.admin_status import AdminStatusService

from test_os_production import configured, login, request  # noqa: F401


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/dr_cloud_sync/static"


def test_dashboard_is_accessible_and_uses_official_brand(configured):
    app, _ = configured
    _, cookie = login(app)
    status, _, body = request(app, "/", cookie=cookie)
    html = body.decode()
    assert status == "200 OK"
    assert 'src="/drcloud-logo.svg"' in html
    assert 'alt="Logo officiel Dr Cloud"' in html
    assert 'aria-label="Navigation principale"' in html
    assert 'href="/administration"' in html
    assert request(app, "/drcloud-logo.svg", cookie=cookie)[0] == "200 OK"


def test_dashboard_only_contains_real_routes_and_no_fictional_metrics():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    for route in ('href="/"', 'href="/roadmap"', 'href="/catalogue"',
                  'href="/inventaire"', 'href="/administration"'):
        assert route in html
    assert 'href="/modules/' not in html
    for fictional in ("chiffre d’affaires", "marge", "ventes", "clients"):
        assert fictional not in html.casefold()
    assert "Données non disponibles pour le moment" in html
    assert "Aucun classement disponible" in html


def test_dashboard_api_exposes_real_system_and_storage_data(configured, tmp_path):
    app, settings = configured
    app.admin_status = AdminStatusService(
        settings.database,
        backup_root=tmp_path / "missing-backups",
        deployment_marker=tmp_path / "missing-marker",
        disk_usage=lambda _: type("Usage", (), {"total": 1000, "used": 420, "free": 580})(),
    )
    _, cookie = login(app)
    status, _, body = request(app, "/api/dashboard", cookie=cookie)
    payload = json.loads(body)
    assert status == "200 OK"
    assert payload["catalogue"] == len(app.service.items)
    assert payload["inventory"] == {
        "session": app.service.session(), "progress": app.service.progress()
    }
    assert payload["systems"]["database"]["available"] is True
    assert payload["systems"]["system"]["disk"] == {
        "total_bytes": 1000, "used_bytes": 420,
        "available_bytes": 580, "used_percent": 42.0,
    }
    assert payload["systems"]["backup"]["status"] == "unknown"


def test_administration_keeps_status_cards_and_uses_same_brand(configured):
    app, _ = configured
    _, cookie = login(app)
    status, _, body = request(app, "/administration", cookie=cookie)
    html = body.decode()
    assert status == "200 OK"
    assert 'src="/drcloud-logo.svg"' in html
    for section in ("application", "database", "backup", "deployment", "system"):
        assert f'data-section="{section}"' in html
