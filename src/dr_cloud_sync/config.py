"""Environment-only application configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when required, safe configuration is missing."""


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_key: str
    database: Path
    timeout_seconds: float = 30.0
    page_size: int = 100

    @classmethod
    def from_env(cls) -> "Settings":
        api_url = os.getenv("PRESTASHOP_API_URL", "https://dr-cloudshop.com/api").rstrip("/")
        api_key = os.getenv("PRESTASHOP_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("PRESTASHOP_API_KEY est obligatoire")
        parsed = urlparse(api_url)
        if parsed.scheme != "https" or not parsed.netloc:
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

