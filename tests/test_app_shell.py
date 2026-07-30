from pathlib import Path

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
