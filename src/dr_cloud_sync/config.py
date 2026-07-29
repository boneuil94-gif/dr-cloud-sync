"""Environment-only application configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_PRESTASHOP_API_URL = "https://dr-cloudshop.com/api"


class ConfigurationError(ValueError):
    """Raised when required, safe configuration is missing."""


def resolve_prestashop_api_url(value: str | None = None) -> str:
    """Resolve and validate the shared PrestaShop Webservice base URL."""
    api_url = (value if value is not None else os.getenv("PRESTASHOP_API_URL", "")).strip()
    api_url = (api_url or DEFAULT_PRESTASHOP_API_URL).rstrip("/")
    parsed = urlparse(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("Configuration PrestaShop invalide : URL API absolue requise")
    return api_url


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_key: str
    database: Path
    timeout_seconds: float = 30.0
    page_size: int = 100

    @classmethod
    def from_env(cls) -> "Settings":
        api_url = resolve_prestashop_api_url()
        api_key = os.getenv("PRESTASHOP_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("PRESTASHOP_API_KEY est obligatoire")
        if urlparse(api_url).scheme != "https":
            raise ConfigurationError("PRESTASHOP_API_URL doit être une URL HTTPS")
        timeout = float(os.getenv("PRESTASHOP_TIMEOUT_SECONDS", "30"))
        page_size = int(os.getenv("PRESTASHOP_PAGE_SIZE", "100"))
        if timeout <= 0 or not 1 <= page_size <= 1000:
            raise ConfigurationError("timeout doit être positif et page_size compris entre 1 et 1000")
        return cls(
            api_url=api_url,
            api_key=api_key,
            database=Path(os.getenv("DR_CLOUD_SYNC_DB", "./dr-cloud-sync.sqlite3")),
            timeout_seconds=timeout,
            page_size=page_size,
        )
