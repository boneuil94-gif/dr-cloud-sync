"""Shared, fail-fast SQLite schema validation and additive migration."""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3


class SchemaMismatchError(RuntimeError):
    """The live database cannot safely be brought to the declared model."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    not_null: bool
    default: str | None
    primary_key: bool


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _columns(db: sqlite3.Connection, table: str) -> tuple[Column, ...]:
    return tuple(Column(row[1], row[2] or "", bool(row[3]), row[4], bool(row[5]))
                 for row in db.execute(f'PRAGMA table_info("{table}")'))


def ensure_schema(db: sqlite3.Connection, schema: str, *, owner: str) -> None:
    """Create tables, add safe missing columns, and reject unsafe drift."""
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(schema)
        # Create missing tables first, but defer indexes until old tables have
        # received their missing columns (an index may itself reference one).
        for statement in schema.split(";"):
            sql = statement.strip()
            if sql.upper().startswith("CREATE TABLE") or sql.upper().startswith("PRAGMA"):
                db.execute(sql)
        for table in sorted(_tables(reference)):
            actual_columns = {column.name: column for column in _columns(db, table)}
            actual = set(actual_columns)
            for column in _columns(reference, table):
                if column.name in actual:
                    live = actual_columns[column.name]
                    if column.type and live.type.upper() != column.type.upper():
                        raise SchemaMismatchError(
                            f"{owner}: table {table!r} column {column.name!r} has type "
                            f"{live.type!r}, expected {column.type!r}"
                        )
                    continue
                if column.primary_key or (column.not_null and column.default is None):
                    raise SchemaMismatchError(
                        f"{owner}: table {table!r} is missing required column "
                        f"{column.name!r}; automatic migration is unsafe"
                    )
                declaration = column.type
                if column.not_null:
                    declaration += " NOT NULL"
                if column.default is not None:
                    declaration += f" DEFAULT {column.default}"
                db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column.name}" {declaration}')
                actual.add(column.name)
            missing = [column.name for column in _columns(reference, table)
                       if column.name not in {item.name for item in _columns(db, table)}]
            if missing:
                raise SchemaMismatchError(f"{owner}: table {table!r} remains incomplete: {missing}")
        db.executescript(schema)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        reference.close()
