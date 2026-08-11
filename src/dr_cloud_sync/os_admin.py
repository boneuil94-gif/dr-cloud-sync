"""Idempotent catalogue initialization and secret-free SQLite backups."""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from .admin_status import application_metadata
from .backup_service import BackupService


def init_catalog(source: Path, report: Path, database: Path) -> int:
    raw = json.loads(source.read_text(encoding="utf-8")); rows = raw.get("mappings", raw) if isinstance(raw, dict) else raw
    validation = json.loads(report.read_text(encoding="utf-8"))
    if validation.get("ready_for_inventory") is not True or not isinstance(rows, list) or not rows:
        raise ValueError("Un mapping final validé et non vide est requis")
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database)
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO schema_version VALUES(1, datetime('now'))")
        db.execute("CREATE TABLE IF NOT EXISTS drcloud_products(drcloud_product_key TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)")
        for row in rows:
            key = row.get("drcloud_product_key") or f"drc:{row['prestashop_key']}"
            item = {**row, "drcloud_product_key": key}
            # Bootstrap only. Runtime edits and lifecycle state remain authoritative.
            db.execute("INSERT OR IGNORE INTO drcloud_products(drcloud_product_key,data,updated_at) VALUES(?,?,datetime('now'))", (key, json.dumps(item, ensure_ascii=False)))
    count = db.execute("SELECT count(*) FROM drcloud_products").fetchone()[0]; db.close()
    # Keep validated input beside the persistent DB for the inventory service.
    shutil.copyfile(source, database.parent / "catalogue.json")
    shutil.copyfile(report, database.parent / "catalogue-report.json")
    return count


def backup(database: Path, destination: Path, *, environment: str, safe_mode: bool,
           reason: str = "MANUAL", catalogue: Path | None = None,
           mapping_report: Path | None = None) -> Path:
    result = BackupService(destination).create(database, reason=reason, environment=environment,
        safe_mode=safe_mode, application=application_metadata(), catalogue=catalogue,
        mapping_report=mapping_report)
    return Path(destination) / result["backup_id"]


def restore_backup(source: Path, data_dir: Path) -> None:
    """Restore a stopped instance after validating the complete DB/media bundle."""
    metadata=json.loads((source/"metadata.json").read_text(encoding="utf-8"))
    database=source/"drcloud.db"
    required=metadata.get("required_runtime_files",[])
    if required != ["drcloud.db","catalogue.json","catalogue-report.json"] or not all((source/name).is_file() for name in required) or metadata.get("media",{}).get("included") is not True:
        raise ValueError("Backup runtime + médias incomplet")
    for item in metadata["media"].get("files",[]):
        candidate=(source/item["path"]).resolve()
        if source.resolve() not in candidate.parents or not candidate.is_file(): raise ValueError("Média de backup manquant")
        if candidate.stat().st_size!=item["size"] or hashlib.sha256(candidate.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("Média de backup corrompu")
    data_dir.mkdir(parents=True,exist_ok=True)
    temporary=data_dir/"drcloud.db.restore"
    shutil.copyfile(database,temporary); temporary.replace(data_dir/"drcloud.db")
    for name in ("catalogue.json","catalogue-report.json"):
        shutil.copyfile(source/name,data_dir/name)
    if (source/"media").is_dir():
        media_tmp=data_dir/"media.restore"
        if media_tmp.exists(): shutil.rmtree(media_tmp)
        shutil.copytree(source/"media",media_tmp)
        current=data_dir/"media"
        if current.exists(): current.rename(data_dir/"media.before-restore")
        media_tmp.replace(current)
