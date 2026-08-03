"""Comparable SQLite file diagnostics for every runtime entry point."""
from __future__ import annotations
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

def database_path(db: sqlite3.Connection) -> Path:
    row = next(row for row in db.execute("PRAGMA database_list") if row[1] == "main")
    if not row[2]: raise ValueError("SQLite in-memory databases have no file identity")
    return Path(row[2]).resolve()

def sqlite_file_diagnostic(path: Path, *, role: str) -> dict:
    absolute = Path(path).resolve()
    digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
    with sqlite3.connect(f"file:{absolute}?mode=ro", uri=True) as db:
        return {"role": role, "absolute_path": str(absolute), "sha256": digest,
            "user_version": db.execute("PRAGMA user_version").fetchone()[0],
            "sumup_transactions": [tuple(row) for row in db.execute("PRAGMA table_info(sumup_transactions)")],
            "sumup_payouts": [tuple(row) for row in db.execute("PRAGMA table_info(sumup_payouts)")]}

def register_runtime(db: sqlite3.Connection, role: str) -> None:
    """Persist the path; recompute the mutable file hash when diagnostics are read."""
    path = database_path(db)
    db.execute("""CREATE TABLE IF NOT EXISTS runtime_sqlite_consumers(
        role TEXT PRIMARY KEY, absolute_path TEXT NOT NULL, seen_at TEXT NOT NULL)""")
    db.execute("""INSERT INTO runtime_sqlite_consumers(role,absolute_path,seen_at) VALUES(?,?,?)
        ON CONFLICT(role) DO UPDATE SET absolute_path=excluded.absolute_path,seen_at=excluded.seen_at""",
        (role, str(path), datetime.now(timezone.utc).isoformat()))
    db.commit()

def runtime_diagnostics(db: sqlite3.Connection) -> list[dict]:
    register_runtime(db, "web")
    results=[]
    for role,path,seen_at in db.execute("SELECT role,absolute_path,seen_at FROM runtime_sqlite_consumers ORDER BY role"):
        item=sqlite_file_diagnostic(Path(path),role=role);item["seen_at"]=seen_at;results.append(item)
    return results
