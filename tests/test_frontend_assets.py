import pytest

from frontend_assets import assert_no_frontend_secrets, frontend_text_assets


def test_frontend_text_collection_ignores_binary_and_keeps_secret_checks(tmp_path):
    html = tmp_path / "index.html"
    css = tmp_path / "styles.css"
    script = tmp_path / "nested" / "app.js"
    png = tmp_path / "drcloud-logo.png"
    html.write_text("<main>DrCloud OS</main>", encoding="utf-8")
    css.write_text("body { color: green; }", encoding="utf-8")
    script.parent.mkdir()
    script.write_text("const exposed = 'PRESTASHOP_API_KEY';", encoding="utf-8")
    png.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x80")

    collected = list(frontend_text_assets(tmp_path))

    assert collected == [html, script, css]
    assert png not in collected
    with pytest.raises(AssertionError, match="PRESTASHOP_API_KEY"):
        assert_no_frontend_secrets(tmp_path, ("PRESTASHOP_API_KEY",))


def test_declared_text_assets_must_be_valid_utf8(tmp_path):
    (tmp_path / "invalid.js").write_bytes(b"\xff\xfe")

    with pytest.raises(UnicodeDecodeError):
        assert_no_frontend_secrets(tmp_path, ("API_KEY",))
