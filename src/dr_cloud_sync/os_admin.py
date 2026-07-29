"""Idempotent catalogue initialization and secret-free SQLite backups."""
from __future__ import annotations
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path


def init_catalog(source: Path, report: Path, database: Path) -> int:
    raw = json.loads(source.read_text(encoding="utf-8")); rows = raw.get("mappings", raw) if isinstance(raw, dict) else raw
    validation = json.loads(report.read_text(encoding="utf-8"))
    if validation.get("ready_for_inventory") is not True or not isinstance(rows, list) or len(rows) != 478:
        raise ValueError("Le mapping final validé de 478 articles est requis")
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database)
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO schema_version VALUES(1, datetime('now'))")
        db.execute("CREATE TABLE IF NOT EXISTS drcloud_products(drcloud_product_key TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)")
        for row in rows:
            key = row.get("drcloud_product_key") or f"drc:{row['prestashop_key']}"
            item = {**row, "drcloud_product_key": key}
            db.execute("INSERT INTO drcloud_products VALUES(?,?,datetime('now')) ON CONFLICT(drcloud_product_key) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at", (key, json.dumps(item, ensure_ascii=False)))
    count = db.execute("SELECT count(*) FROM drcloud_products").fetchone()[0]; db.close()
    if count != 478: raise ValueError(f"Catalogue inattendu après import: {count}")
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
    metadata = {"application":"drcloud-os", "version":version("dr-cloud-sync"), "created_at":stamp,
                "configuration":{"DRCLOUD_ENV":environment,"DRCLOUD_SAFE_MODE":safe_mode,"BARCODE_SYNC_MODE":"dry-run"}}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return target
