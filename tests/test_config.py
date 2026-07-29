import pytest

from dr_cloud_sync.config import (ConfigurationError, DEFAULT_PRESTASHOP_API_URL,
                                  resolve_prestashop_api_url)


def test_missing_prestashop_url_uses_existing_project_default(monkeypatch):
    monkeypatch.delenv("PRESTASHOP_API_URL", raising=False)
    assert resolve_prestashop_api_url() == DEFAULT_PRESTASHOP_API_URL


def test_explicit_valid_prestashop_url_is_used():
    assert resolve_prestashop_api_url("https://example.test/api/") == "https://example.test/api"


def test_empty_prestashop_variable_uses_existing_project_default(monkeypatch):
    monkeypatch.setenv("PRESTASHOP_API_URL", "")
    assert resolve_prestashop_api_url() == DEFAULT_PRESTASHOP_API_URL


@pytest.mark.parametrize("value", ["relative/api", "/api"])
def test_relative_prestashop_url_fails_before_network(value):
    with pytest.raises(ConfigurationError, match="URL API absolue requise"):
        resolve_prestashop_api_url(value)
