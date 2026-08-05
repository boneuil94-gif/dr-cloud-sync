"""Read-only SQLite schema diagnostics for production drift checks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class ExpectedSchema:
    name: str
    schema: str


def _connect_reference(schemas: Iterable[ExpectedSchema]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for item in schemas:
        db.executescript(item.schema)
    return db


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(db: sqlite3.Connection, table: str) -> dict[str, dict]:
    return {row[1]: {"name": row[1], "type": row[2], "notnull": bool(row[3]), "default": row[4], "pk": bool(row[5])}
            for row in db.execute(f'PRAGMA table_info("{table}")')}


def _indexes(db: sqlite3.Connection, table: str) -> dict[str, dict]:
    indexes = {}
    for row in db.execute(f'PRAGMA index_list("{table}")'):
        name = row[1]
        if name.startswith("sqlite_autoindex_"):
            continue
        indexes[name] = {"name": name, "unique": bool(row[2]), "columns": [info[2] for info in db.execute(f'PRAGMA index_info("{name}")')]}
    return indexes


def diagnose_schema(db: sqlite3.Connection, schemas: Iterable[ExpectedSchema], *, scope: str = "global") -> dict:
    """Compare declared schemas with the live database without mutating it."""
    checked_at = datetime.now(timezone.utc).isoformat()
    schemas = tuple(schemas)
    expected_fingerprint = hashlib.sha256("\n".join(item.schema for item in schemas).encode()).hexdigest()
    try:
        reference = _connect_reference(schemas)
        expected_tables = _tables(reference)
        actual_tables = _tables(db)
        tables = []
        for table in sorted(expected_tables):
            expected_columns = _columns(reference, table)
            actual_columns = _columns(db, table) if table in actual_tables else {}
            expected_indexes = _indexes(reference, table)
            actual_indexes = _indexes(db, table) if table in actual_tables else {}
            missing_columns = sorted(set(expected_columns) - set(actual_columns))
            extra_columns = sorted(set(actual_columns) - set(expected_columns))
            missing_indexes = sorted(set(expected_indexes) - set(actual_indexes))
            tables.append({
                "table": table,
                "status": "DRIFT" if table not in actual_tables or missing_columns or missing_indexes else "OK",
                "expected_columns": list(expected_columns),
                "actual_columns": list(actual_columns),
                "missing_columns": missing_columns,
                "extra_columns": extra_columns,
                "expected_indexes": list(expected_indexes),
                "actual_indexes": list(actual_indexes),
                "missing_indexes": missing_indexes,
                "missing_table": table not in actual_tables,
            })
        drift = [item for item in tables if item["status"] != "OK"]
        observed_payload = json.dumps({table: sorted(_columns(db, table)) for table in sorted(expected_tables & actual_tables)}, sort_keys=True)
        return {"scope": scope, "status": "DRIFT" if drift else "OK", "checked_at": checked_at,
                "expected_fingerprint": expected_fingerprint, "observed_fingerprint": hashlib.sha256(observed_payload.encode()).hexdigest(),
                "tables": tables, "drift": drift}
    except sqlite3.OperationalError as exc:
        return {"scope": scope, "status": "UNAVAILABLE", "checked_at": checked_at, "error": str(exc),
                "expected_fingerprint": expected_fingerprint, "observed_fingerprint": None, "tables": [], "drift": []}
    except Exception as exc:
        return {"scope": scope, "status": "ERROR", "checked_at": checked_at, "error": exc.__class__.__name__,
                "expected_fingerprint": expected_fingerprint, "observed_fingerprint": None, "tables": [], "drift": []}


class SchemaDriftError(RuntimeError):
    """Raised to block only the affected job when required schema is drifting."""
    retryable = False
    operator_safe = True
    diagnostic = {"category": "SCHEMA_DRIFT", "stage": "schema-check"}

    def __init__(self, diagnostic: dict):
        super().__init__("SCHEMA_DRIFT")
        self.schema_diagnostic = diagnostic
