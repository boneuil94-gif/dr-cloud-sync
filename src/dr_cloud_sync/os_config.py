"""Production configuration and safe defaults for DrCloud OS."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OSSettings:
    environment: str
    secret_key: str
    admin_username: str
    admin_password: str
    data_dir: Path
    host: str
    port: int
    safe_mode: bool
    trust_proxy: bool

    @classmethod
    def from_env(cls, *, require_secrets: bool = True) -> "OSSettings":
        environment = os.environ.get("DRCLOUD_ENV", "development").lower()
        settings = cls(
            environment=environment,
            secret_key=os.environ.get("DRCLOUD_SECRET_KEY", ""),
            admin_username=os.environ.get("DRCLOUD_ADMIN_USERNAME", ""),
            admin_password=os.environ.get("DRCLOUD_ADMIN_PASSWORD", ""),
            data_dir=Path(os.environ.get("DRCLOUD_DATA_DIR", "/data" if environment == "production" else ".data")),
            host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")),
            safe_mode=env_bool("DRCLOUD_SAFE_MODE", True),
            trust_proxy=env_bool("DRCLOUD_TRUST_PROXY", False),
        )
        if require_secrets and not all((settings.secret_key, settings.admin_username, settings.admin_password)):
            raise ValueError("DRCLOUD_SECRET_KEY, DRCLOUD_ADMIN_USERNAME et DRCLOUD_ADMIN_PASSWORD sont requis")
        if settings.environment == "production" and len(settings.secret_key) < 32:
            raise ValueError("DRCLOUD_SECRET_KEY doit contenir au moins 32 caractères en production")
        return settings

    @property
    def database(self) -> Path:
        return self.data_dir / "drcloud.db"

