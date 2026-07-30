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


def test_registry_declares_every_target_and_only_five_available_modules():
    assert [module.label for module in MODULES] == [
        "Tableau de bord", "Roadmap", "Catalogue", "Inventaire", "Stock",
        "Achats", "Ventes", "Finance", "Clients", "Marketing",
        "Synchronisations", "Automatisations + IA", "Administration",
        "Sécurité + utilisateurs", "Production",
    ]
    assert set(available_pages()) == {
        "dashboard.html", "roadmap.html", "catalogue.html", "inventory.html",
        "administration.html",
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
    assert navigation.count("À venir") == 10
    assert navigation.count("<a href=") == 5


def test_future_routes_are_not_registered_or_rendered(configured):
    app, _ = configured
    _, cookie = login(app)
    future_routes = (
        "/stock", "/achats", "/ventes", "/finance", "/clients", "/marketing",
        "/synchronisations", "/automatisations", "/securite", "/production",
    )
    navigation = render_navigation("stock")
    assert 'aria-current="page"' not in navigation
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
