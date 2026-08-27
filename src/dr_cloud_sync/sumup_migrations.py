"""Versioned, additive migrations for the production SumUp SQLite schema."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from .schema_diagnostics import ExpectedSchema, diagnose_schema


SUMUP_SCHEMA_VERSION = 5
MIGRATION_NAME = "sumup_merchant_resource_timestamps_20260827"


class SumUpSchemaMigrationError(RuntimeError):
    """Raised when a historical schema cannot be upgraded without data loss."""


def _tables(db):
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _columns(db, table):
    return {row[1]: row for row in db.execute(f'PRAGMA table_info("{table}")')}


def _statements(schema):
    return [statement.strip() for statement in schema.split(";") if statement.strip()]


def _additive_declaration(row):
    name, kind, not_null, default, primary_key = row[1], row[2] or "TEXT", bool(row[3]), row[4], bool(row[5])
    if primary_key:
        raise SumUpSchemaMigrationError(
            f"SumUp: table is missing primary-key column {name!r}; additive migration is unsafe"
        )
    declaration = kind
    if not_null:
        # Historical rows need a deterministic neutral value. New writes always
        # provide every required business value explicitly.
        neutral = default
        if neutral is None:
            neutral = "'[]'" if name.endswith("events_json") else "'{}'" if name.endswith("raw_json") else "'EUR'" if name == "currency" else "'MATCHED'" if name == "status" else "'0'" if name in {"amount", "fee"} else "''"
        declaration += f" NOT NULL DEFAULT {neutral}"
    elif default is not None:
        declaration += f" DEFAULT {default}"
    return declaration


def migrate_sumup_schema(db: sqlite3.Connection, schema: str) -> dict:
    """Upgrade all declared SumUp tables atomically and record the migration."""
    reference = sqlite3.connect(":memory:")
    reference.executescript(schema)
    expected_tables = _tables(reference)
    added = []
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS sumup_schema_migrations(
            version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS sumup_schema_checks(
            check_id INTEGER PRIMARY KEY CHECK(check_id=1), checked_at TEXT NOT NULL,
            result TEXT NOT NULL, details_json TEXT NOT NULL)""")
        # CREATE TABLE IF NOT EXISTS is useful for a new database, but it is
        # deliberately followed by an audited ALTER pass for historical ones.
        for statement in _statements(schema):
            if statement.upper().startswith("CREATE TABLE"):
                db.execute(statement)
        for table in sorted(expected_tables):
            actual = _columns(db, table)
            for row in reference.execute(f'PRAGMA table_info("{table}")'):
                name = row[1]
                if name not in actual:
                    db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {_additive_declaration(row)}')
                    added.append(f"{table}.{name}")
        for statement in _statements(schema):
            if statement.upper().startswith("CREATE INDEX") or statement.upper().startswith("CREATE UNIQUE INDEX"):
                db.execute(statement)
        missing = {
            table: sorted(set(_columns(reference, table)) - set(_columns(db, table)))
            for table in expected_tables
        }
        missing = {table: names for table, names in missing.items() if names}
        if missing:
            raise SumUpSchemaMigrationError(f"SumUp schema remains incomplete: {missing}")
        db.execute(f"PRAGMA user_version={SUMUP_SCHEMA_VERSION}")
        db.execute(
            "INSERT OR IGNORE INTO sumup_schema_migrations(version,name,applied_at) VALUES(?,?,?)",
            (SUMUP_SCHEMA_VERSION, MIGRATION_NAME, now),
        )
        diagnostic = diagnose_schema(db, [ExpectedSchema("sumup", schema)], scope="sumup")
        details = json.dumps({"tables_checked": sorted(expected_tables), "missing_columns": sum(len(t["missing_columns"]) for t in diagnostic["tables"]), "status": diagnostic["status"]})
        db.execute("""INSERT INTO sumup_schema_checks(check_id,checked_at,result,details_json)
            VALUES(1,?,'OK',?) ON CONFLICT(check_id) DO UPDATE SET
            checked_at=excluded.checked_at,result=excluded.result,details_json=excluded.details_json""", (now, details))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        reference.close()
    return sumup_schema_diagnostic(db, added_columns=added)


def sumup_schema_diagnostic(db: sqlite3.Connection, *, added_columns=()) -> dict:
    try:
        applied = [dict(zip(("version", "name", "applied_at"), row)) for row in db.execute(
            "SELECT version,name,applied_at FROM sumup_schema_migrations ORDER BY version"
        )]
    except sqlite3.OperationalError:
        applied = []
    try:
        check = db.execute("SELECT checked_at,result FROM sumup_schema_checks WHERE check_id=1").fetchone()
    except sqlite3.OperationalError:
        check = None
    versions = {item["version"] for item in applied}
    global_diagnostic = diagnose_schema(db, [ExpectedSchema("sumup", __import__("dr_cloud_sync.sumup", fromlist=["SCHEMA"]).SCHEMA)], scope="sumup")
    return {
        "schema_version": max(versions, default=0),
        "target_version": SUMUP_SCHEMA_VERSION,
        "applied_migrations": applied,
        "pending_migrations": [] if SUMUP_SCHEMA_VERSION in versions else [MIGRATION_NAME],
        "last_check": {"checked_at": check[0], "result": check[1]} if check else None,
        "added_columns_this_start": list(added_columns),
        "global_status": global_diagnostic["status"],
        "checked_at": global_diagnostic["checked_at"],
        "expected_fingerprint": global_diagnostic["expected_fingerprint"],
        "observed_fingerprint": global_diagnostic["observed_fingerprint"],
        "tables": global_diagnostic["tables"],
        "drift": global_diagnostic["drift"],
    }
