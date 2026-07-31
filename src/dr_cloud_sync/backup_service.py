"""Central, fail-closed and atomic backup storage for DrCloud OS."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

LOG = logging.getLogger("drcloud.os.backup")
BACKUP_UNAVAILABLE_MESSAGE = "Sauvegarde impossible : le stockage des sauvegardes n'est pas disponible."


class BackupUnavailable(RuntimeError):
    """Operator-safe error; the technical cause is only emitted server-side."""


def configured_backup_dir(data_dir: Path) -> Path:
    """Return the sole runtime backup-root configuration."""
    return Path(os.environ.get("DRCLOUD_BACKUP_DIR", str(Path(data_dir) / "backups")))


class BackupService:
    def __init__(self, root: Path):
        self.root = Path(root)

    def health(self, *, create: bool = True) -> dict:
        """Check private read/write storage without leaving a probe behind."""
        try:
            if create:
                self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            if not self.root.is_dir():
                raise NotADirectoryError(self.root)
            # Tighten a directory created by us; never broaden an existing directory.
            current = self.root.stat().st_mode & 0o777
            if current & 0o077:
                self.root.chmod(current & 0o700)
            probe = self.root / f".health-{uuid4().hex}"
            with probe.open("xb") as stream:
                stream.write(b"drcloud-backup-health\n")
                stream.flush(); os.fsync(stream.fileno())
            if probe.read_bytes() != b"drcloud-backup-health\n":
                raise OSError("backup probe verification failed")
            probe.unlink()
            return {"available": True, "status": "ok", "message": "Backup storage : Disponible"}
        except Exception as exc:
            LOG.exception("backup_storage_unavailable root=%s", self.root)
            return {"available": False, "status": "error",
                    "message": "Backup storage : Indisponible"}

    @staticmethod
    def _sqlite_check(path: Path) -> None:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError("backup integrity_check failed")

    def create(self, database: Path, *, reason: str, environment: str,
               safe_mode: bool, application: dict | None = None) -> dict:
        """Create, verify, and atomically publish a complete backup bundle."""
        health = self.health(create=True)
        if not health["available"]:
            raise BackupUnavailable(BACKUP_UNAVAILABLE_MESSAGE)
        backup_id = f"drcloud-os-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
        temporary = self.root / f".{backup_id}.partial"
        target = self.root / backup_id
        try:
            temporary.mkdir(mode=0o700)
            db_target = temporary / "drcloud.db"
            source = sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True)
            copy = sqlite3.connect(db_target)
            try:
                source.backup(copy)
            finally:
                copy.close(); source.close()
            media_source = Path(database).parent / "media"
            if media_source.is_dir():
                shutil.copytree(media_source, temporary / "media", symlinks=False)
            files = []
            for path in sorted(p for p in temporary.rglob("*") if p.is_file()):
                files.append({"path": path.relative_to(temporary).as_posix(),
                              "size": path.stat().st_size,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            self._sqlite_check(db_target)
            created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            metadata = {"backup_id": backup_id, "created_at": created_at,
                        "reason": reason, "status": "SUCCESS", "application": application or {},
                        "configuration": {"environment": environment, "safe_mode": safe_mode},
                        "files": files, "media": {"included": True,
                        "files": [item for item in files if item["path"].startswith("media/")]}}
            metadata_path = temporary / "metadata.json"
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            with metadata_path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            # Re-read the published bundle; SUCCESS is never returned for a partial bundle.
            verified = self.verify(target)
            return verified
        except BackupUnavailable:
            raise
        except Exception as exc:
            LOG.exception("backup_creation_failed backup_id=%s root=%s", backup_id, self.root)
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)
            raise BackupUnavailable(BACKUP_UNAVAILABLE_MESSAGE) from exc

    def verify(self, bundle: Path) -> dict:
        bundle = Path(bundle)
        try:
            if bundle.parent.resolve() != self.root.resolve() or bundle.name.startswith("."):
                raise ValueError("bundle outside backup root")
            metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
            if metadata.get("status") != "SUCCESS" or metadata.get("backup_id") != bundle.name:
                raise ValueError("invalid metadata")
            expected = metadata.get("files")
            if not isinstance(expected, list) or not any(x.get("path") == "drcloud.db" for x in expected):
                raise ValueError("incomplete bundle")
            for item in expected:
                candidate = (bundle / item["path"]).resolve()
                if bundle.resolve() not in candidate.parents or not candidate.is_file():
                    raise ValueError("unsafe or missing component")
                if candidate.stat().st_size != item["size"] or hashlib.sha256(candidate.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("component verification failed")
            self._sqlite_check(bundle / "drcloud.db")
            return {**metadata, "size_bytes": sum(x["size"] for x in expected)}
        except Exception as exc:
            LOG.exception("backup_verification_failed bundle=%s", bundle)
            raise BackupUnavailable(BACKUP_UNAVAILABLE_MESSAGE) from exc

    def successful(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        results = []
        for item in self.root.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue
            try:
                results.append(self.verify(item))
            except BackupUnavailable:
                continue
        return sorted(results, key=lambda row: row["created_at"], reverse=True)
