import json
import struct
from pathlib import Path

from dr_cloud_sync.modules import GROUPS, MODULES, available_pages, render_navigation

from test_os_production import configured, login, request  # noqa: F401


STATIC = Path(__file__).parents[1] / "src/dr_cloud_sync/static"
PAGES = {
    "/": ("Tableau de bord", "/"),
    "/roadmap": ("Roadmap", "/roadmap"),
    "/catalogue": ("Catalogue", "/catalogue"),
    "/inventaire": ("Inventaire", "/inventaire"),
    "/administration": ("Administration", "/administration"),
    "/securite": ("Sécurité", "/securite"),
}


def test_authenticated_pages_share_shell_brand_navigation_and_active_state(configured):
    app, _ = configured
    _, cookie = login(app)
    for path, (title, active_path) in PAGES.items():
        status, _, body = request(app, path, cookie=cookie)
        html = body.decode()
        assert status == "200 OK"
        assert html.count('class="dc-sidebar"') == 1
        assert 'src="/drcloud-logo.png"' in html
        assert '<span class="dc-os-mark">OS</span>' in html
        assert 'action="/logout"' in html
        assert f'<strong>{title}</strong>' in html
        assert f'href="{active_path}" aria-current="page"' in html
        assert 'drcloud-logo.svg' not in html
        assert 'href="/modules/' not in html


def test_shared_shell_exposes_accessible_mobile_drawer_contract(configured):
    app, _ = configured
    _, cookie = login(app)
    status, _, body = request(app, "/inventaire", cookie=cookie)
    html = body.decode()
    assert status == "200 OK"
    assert 'class="dc-sidebar" id="mobileDrawer"' in html
    assert 'class="dc-menu-button" type="button"' in html
    assert 'aria-label="Ouvrir le menu"' in html
    assert 'aria-controls="mobileDrawer"' in html
    assert 'aria-expanded="false"' in html
    assert 'class="dc-drawer-overlay"' in html
    assert '<script src="/app-shell.js"></script>' in html
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html


def test_public_manifest_is_valid_installable_metadata(configured):
    app, _ = configured
    status, headers, body = request(app, "/manifest.webmanifest")
    manifest = json.loads(body)
    assert status == "200 OK"
    assert headers["Content-Type"] == "application/manifest+json"
    assert manifest["name"] == "DrCloud OS"
    assert manifest["short_name"] == "DrCloud OS"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert manifest["background_color"] == "#f5f7f6"
    assert manifest["theme_color"] == "#111513"
    assert manifest["icons"] == [{
        "src": "/drcloud-logo.png",
        "sizes": "500x500",
        "type": "image/png",
        "purpose": "any",
    }]


def test_pwa_runtime_and_icons_are_public_with_correct_types(configured):
    app, _ = configured
    status, headers, body = request(app, "/drcloud-logo.png")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/png"
    assert body.startswith(b"\x89PNG\r\n\x1a\n")

    for path in ("/pwa.js", "/service-worker.js"):
        status, headers, body = request(app, path)
        assert status == "200 OK"
        assert headers["Content-Type"] == "text/javascript; charset=utf-8"
        assert not body.lstrip().lower().startswith(b"<!doctype html")
    assert request(app, "/service-worker.js")[1]["Service-Worker-Allowed"] == "/"


def test_official_icon_has_its_declared_500_pixel_dimensions():
    def png_dimensions(path):
        data = path.read_bytes()
        return struct.unpack(">II", data[16:24])

    assert png_dimensions(STATIC / "drcloud-logo.png") == (500, 500)


def test_service_worker_is_registered_and_strictly_network_only(configured):
    app, _ = configured
    registration = (STATIC / "pwa.js").read_text(encoding="utf-8")
    worker = (STATIC / "service-worker.js").read_text(encoding="utf-8")
    assert "register('/service-worker.js', { scope: '/' })" in registration
    assert ".catch(" in registration
    assert "respondWith(fetch(event.request))" in worker
    assert "caches" not in worker
    assert "cache.put" not in worker.lower()

    login_html = request(app, "/login")[2].decode()
    assert '<link rel="manifest" href="/manifest.webmanifest">' in login_html
    assert '<script src="/pwa.js"></script>' in login_html

    _, cookie = login(app)
    app_html = request(app, "/", cookie=cookie)[2].decode()
    assert '<link rel="manifest" href="/manifest.webmanifest">' in app_html
    assert '<script src="/pwa.js"></script>' in app_html


def test_unauthenticated_start_url_still_redirects_to_public_login(configured):
    app, _ = configured
    status, headers, _ = request(app, "/")
    assert status == "303 See Other"
    assert headers["Location"] == "/login"
    assert request(app, "/login")[0] == "200 OK"


def test_inventory_mobile_scanner_has_manual_fallback_contract(configured):
    app, _ = configured
    _, cookie = login(app)
    html = request(app, "/inventaire", cookie=cookie)[2].decode()
    assert 'id="camera" type="button" aria-label="Scanner un EAN avec la caméra"' in html
    assert '<span>Scanner</span>' in html
    assert 'id="cameraPanel" class="camera-panel" hidden' in html
    assert 'id="closeCamera" type="button"' in html
    script = (STATIC / "inventory.js").read_text(encoding="utf-8")
    assert "Permission caméra refusée" in script
    assert "EAN non reconnu" in script
    assert "getUserMedia" in script and "track.stop()" in script


def test_registry_declares_every_target_and_stock_is_available():
    assert [module.label for module in MODULES] == [
        "Tableau de bord", "Roadmap", "Catalogue", "Inventaire", "Stock",
        "Achats", "Ventes", "Finance", "Clients", "Marketing",
        "Synchronisations", "Automatisations + IA", "Administration",
        "Sécurité", "Production",
    ]
    assert set(available_pages()) == {
            "dashboard.html", "roadmap.html", "catalogue.html", "inventory.html",
            "administration.html", "stock.html", "purchasing.html", "security.html", "marketing.html",
    }
    assert all(module.roadmap_id for module in MODULES if module.id not in {"roadmap", "administration"})


def test_future_modules_are_non_interactive_and_explicitly_unavailable():
    navigation = render_navigation("dashboard")
    assert GROUPS == ("Principal", "Opérations", "Pilotage", "Automatisation", "Système")
    for group in GROUPS:
        assert f'<p class="dc-nav-label">{group}</p>' in navigation
    for module in (module for module in MODULES if not module.available):
        assert module.route is None
        item = navigation.split(f'<span>{module.label}</span>', 1)[0].rsplit(
            '<span class="dc-nav-future"', 1
        )[1]
        assert 'aria-disabled="true"' in item
    assert navigation.count("À venir") == 6
    assert navigation.count("<a href=") == 9


def test_navigation_renders_every_registry_module_once_with_complete_items():
    for active_module in (module for module in MODULES if module.available):
        navigation = render_navigation(active_module.id)
        for module in MODULES:
            assert navigation.count(f"<span>{module.label}</span>") == 1
            assert navigation.count(f'>{module.icon}</span>') == 1

        active_item = navigation.split(' aria-current="page">', 1)[1].split("</a>", 1)[0]
        assert f'<span>{active_module.label}</span>' in active_item
        assert '<span class="dc-nav-icon"' in active_item

        for module in (module for module in MODULES if not module.available):
            after_label = navigation.split(f'<span>{module.label}</span>', 1)[1]
            assert after_label.startswith("<small>À venir</small>")


def test_mobile_drawer_css_keeps_items_compact_visible_and_navigation_scrollable():
    css = (STATIC / "inventory.css").read_text(encoding="utf-8")
    mobile_shell = css.split("/* Mobile application shell.", 1)[1]

    assert ".dc-sidebar .dc-nav{display:block;flex:1 1 auto;min-height:0" in mobile_shell
    assert "overflow-x:hidden;overflow-y:auto" in mobile_shell
    assert ".dc-sidebar .dc-nav a,.dc-sidebar .dc-nav-future{display:flex;align-items:center;flex:none" in mobile_shell
    assert "min-height:46px;height:auto" in mobile_shell
    assert ".dc-sidebar .dc-nav a>span:not(.dc-nav-icon)" in mobile_shell
    assert ".dc-sidebar .dc-nav-icon{display:grid" in mobile_shell
    assert ".dc-sidebar-footer{display:grid;flex:0 0 auto}" in mobile_shell
    item_rule = mobile_shell.split(
        ".dc-sidebar .dc-nav a,.dc-sidebar .dc-nav-future{", 1
    )[1].split("}", 1)[0]
    assert "height:100%" not in item_rule
    assert "flex-grow" not in item_rule


def test_future_routes_are_not_registered_or_rendered(configured):
    app, _ = configured
    _, cookie = login(app)
    future_routes = (
        "/ventes", "/finance", "/clients",
        "/synchronisations", "/automatisations", "/production",
    )
    navigation = render_navigation("stock")
    assert '<a href="/stock" aria-current="page">' in navigation
    for route in future_routes:
        assert f'href="{route}"' not in navigation
        assert request(app, route, cookie=cookie)[0] == "404 Not Found"


def test_login_uses_official_brand_without_application_sidebar(configured):
    app, _ = configured
    status, _, body = request(app, "/login")
    html = body.decode()
    assert status == "200 OK"
    assert 'src="/drcloud-logo.png"' in html
    assert '<span class="dc-os-mark">OS</span>' in html
    assert 'class="dc-sidebar"' not in html


def test_old_logo_is_not_referenced_by_frontend_text_assets():
    for asset in STATIC.iterdir():
        if asset.suffix in {".html", ".css", ".js", ".webmanifest", ".svg"}:
            assert "drcloud-logo.svg" not in asset.read_text(encoding="utf-8")
