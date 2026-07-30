"""Idempotent catalogue initialization and secret-free SQLite backups."""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
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
    metadata = {"application":"drcloud-os", "version":application_metadata()["version"],
                "commit":os.environ.get("DRCLOUD_BUILD_COMMIT", "unknown"), "created_at":stamp,
                "configuration":{"DRCLOUD_ENV":environment,"DRCLOUD_SAFE_MODE":safe_mode,"BARCODE_SYNC_MODE":"dry-run"}}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return target
