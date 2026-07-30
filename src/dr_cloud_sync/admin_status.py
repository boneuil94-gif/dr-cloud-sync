"""Read-only, fault-tolerant observability for the Administration view."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

LOG = logging.getLogger("drcloud.os.admin")
VALID_STATUSES = {"ok", "warning", "error", "unknown"}


def application_metadata() -> dict:
    """Single source of truth shared by public health and private supervision."""
    try:
        application_version = version("dr-cloud-sync")
    except PackageNotFoundError:  # Source-tree execution without an installed wheel.
        from . import __version__
        application_version = __version__
    return {"version": application_version,
            "commit": os.environ.get("DRCLOUD_BUILD_COMMIT", "unknown"),
            "build_date": os.environ.get("DRCLOUD_BUILD_DATE", "unknown")}


class AdminStatusService:
    """Collect a deliberately small allow-list of non-sensitive runtime data."""

    def __init__(self, database: Path, *, backup_root: Path | None = None,
                 deployment_marker: Path | None = None, now=None, disk_usage=None):
        self.database = Path(database)
        self.backup_root = Path(backup_root or os.environ.get("DRCLOUD_BACKUP_ROOT", "/backups"))
        self.deployment_marker = Path(deployment_marker or os.environ.get(
            "DRCLOUD_DEPLOYMENT_MARKER",
            "/run/drcloud-deployment/last-successful-commit"))
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.disk_usage = disk_usage or shutil.disk_usage

    def collect(self) -> dict:
        metadata = application_metadata()
        sections = {
            "application": {"status": "ok", **metadata},
            "database": self._safe("database", self._database),
            "backup": self._safe("backup", self._backup),
            "deployment": self._safe("deployment", lambda: self._deployment(metadata)),
            "system": self._safe("system", self._system),
        }
        sections["status"] = self._overall(sections.values())
        sections["checked_at"] = self.now().isoformat().replace("+00:00", "Z")
        return sections

    def _safe(self, name, collector):
        try:
            value = collector()
            if value.get("status") not in VALID_STATUSES:
                value["status"] = "unknown"
            return value
        except Exception:
            LOG.exception("admin_status_collection_failed component=%s", name)
            return {"status": "unknown", "available": False}

    def _database(self):
        if not self.database.is_file():
            return {"status": "error", "available": False, "size_bytes": None,
                    "check": "unavailable"}
        size = self.database.stat().st_size
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True, timeout=1)
        try:
            connection.execute("SELECT 1").fetchone()
            result = connection.execute("PRAGMA quick_check(1)").fetchone()
        finally:
            connection.close()
        check = result[0] if result else "unknown"
        return {"status": "ok" if check == "ok" else "warning", "available": True,
                "size_bytes": max(0, size), "check": check}

    def _backup(self):
        if not self.backup_root.is_dir():
            return {"status": "unknown", "available": False, "count": 0,
                    "last_backup_at": None, "age_seconds": None}
        candidates = []
        for item in self.backup_root.iterdir():
            if not item.is_dir() or not (item / "drcloud.db").is_file():
                continue
            created = datetime.fromtimestamp((item / "drcloud.db").stat().st_mtime, timezone.utc)
            metadata = item / "metadata.json"
            if metadata.is_file():
                try:
                    import json
                    raw = json.loads(metadata.read_text(encoding="utf-8"))
                    stamp = raw.get("created_at")
                    if stamp:
                        created = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except (OSError, ValueError, TypeError):
                    LOG.warning("invalid_backup_metadata backup=%s", item.name)
            candidates.append(created)
        if not candidates:
            return {"status": "warning", "available": True, "count": 0,
                    "last_backup_at": None, "age_seconds": None}
        latest = max(candidates); age = max(0, int((self.now() - latest).total_seconds()))
        status = "ok" if age <= 86400 else "warning" if age <= 172800 else "error"
        return {"status": status, "available": True, "count": len(candidates),
                "last_backup_at": latest.isoformat().replace("+00:00", "Z"), "age_seconds": age}

    def _deployment(self, metadata):
        served = self._valid_commit(metadata["commit"])
        successful = "unknown"
        try:
            candidate = self.deployment_marker.read_text(encoding="utf-8").strip()
            successful = self._valid_commit(candidate)
        except (OSError, UnicodeError):
            pass
        consistency = "unknown" if "unknown" in (served, successful) else "match" if served == successful else "mismatch"
        status = "unknown" if consistency == "unknown" else "ok" if consistency == "match" else "warning"
        return {"status": status, "served_commit": served,
                "last_successful_commit": successful, "consistency": consistency,
                "build_date": metadata["build_date"], "runtime": "application"}

    @staticmethod
    def _valid_commit(value):
        """Return a canonical full Git SHA, never unchecked marker contents."""
        if isinstance(value, str) and len(value) == 40 and all(
                char in "0123456789abcdefABCDEF" for char in value):
            return value.lower()
        return "unknown"

    def _system(self):
        target = self.database.parent if self.database.parent.exists() else Path.cwd()
        usage = self.disk_usage(target)
        total, used, free = (int(usage.total), int(usage.used), int(usage.free))
        if total <= 0 or min(used, free) < 0:
            return {"status": "unknown", "disk": {"total_bytes": None, "used_bytes": None,
                    "available_bytes": None, "used_percent": None}}
        percent = min(100, max(0, round(used * 100 / total, 1)))
        status = "error" if percent >= 95 else "warning" if percent >= 85 else "ok"
        return {"status": status, "disk": {"total_bytes": total, "used_bytes": used,
                "available_bytes": free, "used_percent": percent}}

    @staticmethod
    def _overall(values):
        statuses = {value.get("status", "unknown") for value in values if isinstance(value, dict)}
        for status in ("error", "warning", "unknown", "ok"):
            if status in statuses:
                return status
        return "unknown"
