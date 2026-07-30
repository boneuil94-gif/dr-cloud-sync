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


def backup(database: Path, destination: Path, *, environment: str, safe_mode: bool) -> Path:
    if not database.exists(): raise FileNotFoundError(f"Base absente: {database}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination / f"drcloud-os-backup-{stamp}"; target.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(database); copy = sqlite3.connect(target / "drcloud.db")
    with copy: source.backup(copy)
    source.close(); copy.close()
    media_source=database.parent/"media"
    media_target=target/"media"
    if media_source.is_dir(): shutil.copytree(media_source,media_target,symlinks=False)
    media_files=[]
    if media_target.is_dir():
        for path in sorted(p for p in media_target.rglob("*") if p.is_file()):
            media_files.append({"path":path.relative_to(target).as_posix(),"size":path.stat().st_size,
                                "sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    metadata = {"application":"drcloud-os", "version":application_metadata()["version"],
                "commit":os.environ.get("DRCLOUD_BUILD_COMMIT", "unknown"), "created_at":stamp,
                "media":{"included":True,"files":media_files},
                "configuration":{"DRCLOUD_ENV":environment,"DRCLOUD_SAFE_MODE":safe_mode,"BARCODE_SYNC_MODE":"dry-run"}}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return target


def restore_backup(source: Path, data_dir: Path) -> None:
    """Restore a stopped instance after validating the complete DB/media bundle."""
    metadata=json.loads((source/"metadata.json").read_text(encoding="utf-8"))
    database=source/"drcloud.db"
    if not database.is_file() or metadata.get("media",{}).get("included") is not True:
        raise ValueError("Backup DB + médias incomplet")
    for item in metadata["media"].get("files",[]):
        candidate=(source/item["path"]).resolve()
        if source.resolve() not in candidate.parents or not candidate.is_file(): raise ValueError("Média de backup manquant")
        if candidate.stat().st_size!=item["size"] or hashlib.sha256(candidate.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("Média de backup corrompu")
    data_dir.mkdir(parents=True,exist_ok=True)
    temporary=data_dir/"drcloud.db.restore"
    shutil.copyfile(database,temporary); temporary.replace(data_dir/"drcloud.db")
    if (source/"media").is_dir():
        media_tmp=data_dir/"media.restore"
        if media_tmp.exists(): shutil.rmtree(media_tmp)
        shutil.copytree(source/"media",media_tmp)
        current=data_dir/"media"
        if current.exists(): current.rename(data_dir/"media.before-restore")
        media_tmp.replace(current)
