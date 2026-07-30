"""Persistence boundaries and the replaceable SQLite DrCloud OS adapter."""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from typing import Protocol

from .domain import (ActivityLog, AssignmentStatus, BarcodeAssignment, MovementStatus,
                     MovementType, Product, RemoteStatus, StockMovement)


STOCK_MOVEMENT_COLUMNS = {
    "prestashop_key": "TEXT",
    "drcloud_product_key": "TEXT",
    "source_type": "TEXT",
    "idempotency_key": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'PENDING'",
    "applied_at": "TEXT",
    "actor": "TEXT",
    "result_message": "TEXT",
}


def ensure_stock_movements_schema(db: sqlite3.Connection) -> None:
    """Create or additively migrate the stock ledger schema.

    Legacy rows retain their ``prestashop_key`` and receive deterministic
    DrCloud and idempotency identities derived from their existing primary key.
    """
    db.execute("""CREATE TABLE IF NOT EXISTS stock_movements(
      id TEXT PRIMARY KEY, prestashop_key TEXT, drcloud_product_key TEXT,
      quantity_delta INTEGER NOT NULL, movement_type TEXT NOT NULL,
      source_type TEXT, source_id TEXT, idempotency_key TEXT,
      status TEXT NOT NULL DEFAULT 'PENDING', created_at TEXT NOT NULL,
      validated_at TEXT, applied_at TEXT, actor TEXT, result_message TEXT)""")
    existing = {row[1] for row in db.execute("PRAGMA table_info(stock_movements)")}
    for name, declaration in STOCK_MOVEMENT_COLUMNS.items():
        if name not in existing:
            db.execute(f"ALTER TABLE stock_movements ADD COLUMN {name} {declaration}")
    db.execute("""UPDATE stock_movements
                  SET drcloud_product_key='drc:' || prestashop_key
                  WHERE drcloud_product_key IS NULL AND prestashop_key IS NOT NULL""")
    db.execute("""UPDATE stock_movements
                  SET source_type='LEGACY', idempotency_key='legacy:' || id
                  WHERE source_type IS NULL AND idempotency_key IS NULL""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_stock_movements_idempotency
                  ON stock_movements(source_type, idempotency_key)
                  WHERE source_type IS NOT NULL AND idempotency_key IS NOT NULL""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_stock_movements_product ON stock_movements(drcloud_product_key)")
    db.commit()


class StockMovementRepository(Protocol):
    """Persistence port for append-only stock movements."""
    def append(self, movement: StockMovement) -> None: ...
    def get(self, identifier: str) -> StockMovement | None: ...
    def by_idempotency_key(self, source_type: str, key: str) -> StockMovement | None: ...
    def list(self) -> list[StockMovement]: ...


class DuplicateStockMovement(Exception):
    """The database rejected an append because an immutable identity exists."""


class SQLiteStockMovementRepository:
    """SQLite adapter; uniqueness is enforced by the database, including races."""
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        ensure_stock_movements_schema(self.db)

    def append(self, movement: StockMovement) -> None:
        try:
            with self.db:
                self.db.execute("""INSERT INTO stock_movements(
                  id,drcloud_product_key,quantity_delta,movement_type,source_type,source_id,
                  idempotency_key,status,created_at,validated_at,applied_at,actor,result_message)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    movement.id, movement.drcloud_product_key, movement.quantity_delta,
                    movement.movement_type.value, movement.source_type, movement.source_id,
                    movement.idempotency_key, movement.status.value, movement.created_at,
                    movement.validated_at, movement.applied_at, movement.actor,
                    movement.result_message))
        except sqlite3.IntegrityError as exc:
            raise DuplicateStockMovement from exc

    @staticmethod
    def _movement(row: sqlite3.Row | None) -> StockMovement | None:
        if row is None:
            return None
        return StockMovement(
            id=row["id"], drcloud_product_key=row["drcloud_product_key"],
            quantity_delta=row["quantity_delta"], movement_type=MovementType(row["movement_type"]),
            source_type=row["source_type"], source_id=row["source_id"],
            idempotency_key=row["idempotency_key"], status=MovementStatus(row["status"]),
            created_at=row["created_at"], validated_at=row["validated_at"],
            applied_at=row["applied_at"], actor=row["actor"], result_message=row["result_message"])

    def get(self, identifier: str) -> StockMovement | None:
        return self._movement(self.db.execute("SELECT * FROM stock_movements WHERE id=?", (identifier,)).fetchone())

    def by_idempotency_key(self, source_type: str, key: str) -> StockMovement | None:
        row = self.db.execute("""SELECT * FROM stock_movements
                                 WHERE source_type=? AND idempotency_key=?""", (source_type, key)).fetchone()
        return self._movement(row)

    def list(self) -> list[StockMovement]:
        return [self._movement(row) for row in self.db.execute("SELECT * FROM stock_movements ORDER BY created_at,id")]  # type: ignore[misc]


class CatalogRepository(Protocol):
    def all(self) -> list[Product]: ...
    def get(self, key: str) -> Product | None: ...
    def by_ean(self, ean: str) -> list[Product]: ...
    def set_ean(self, key: str, ean: str) -> None: ...


class AuditRepository(Protocol):
    def save_assignment(self, assignment: BarcodeAssignment) -> None: ...
    def assignment(self, identifier: str) -> BarcodeAssignment | None: ...
    def add_activity(self, activity: ActivityLog) -> None: ...
    def activities(self) -> list[ActivityLog]: ...


class MemoryCatalogRepository:
    def __init__(self, products: list[Product]):
        self.products = {p.drcloud_product_key: p for p in products}
    def all(self) -> list[Product]: return list(self.products.values())
    def get(self, key: str) -> Product | None: return self.products.get(key)
    def by_ean(self, ean: str) -> list[Product]: return [p for p in self.all() if p.ean == ean and ean]
    def set_ean(self, key: str, ean: str) -> None: self.products[key].ean = ean


class MemoryAuditRepository:
    def __init__(self): self.assignments: dict[str, BarcodeAssignment] = {}; self.logs: list[ActivityLog] = []
    def save_assignment(self, assignment: BarcodeAssignment) -> None: self.assignments[assignment.id] = assignment
    def assignment(self, identifier: str) -> BarcodeAssignment | None: return self.assignments.get(identifier)
    def add_activity(self, activity: ActivityLog) -> None: self.logs.append(activity)
    def activities(self) -> list[ActivityLog]: return list(self.logs)


class SQLiteOSRepository(MemoryCatalogRepository, MemoryAuditRepository):
    """One adapter implementing both ports; PostgreSQL can replace it unchanged."""
    def __init__(self, path: Path, products: list[Product]):
        MemoryCatalogRepository.__init__(self, products)
        MemoryAuditRepository.__init__(self)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS barcode_assignments(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_logs(id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalogue_eans(drcloud_product_key TEXT PRIMARY KEY, ean TEXT NOT NULL);
        """)
        for key, ean in self.db.execute("SELECT drcloud_product_key,ean FROM catalogue_eans"):
            if key in self.products: self.products[key].ean = ean

    def set_ean(self, key: str, ean: str) -> None:
        super().set_ean(key, ean)
        with self.db: self.db.execute("INSERT OR REPLACE INTO catalogue_eans VALUES(?,?)", (key, ean))

    def save_assignment(self, assignment: BarcodeAssignment) -> None:
        super().save_assignment(assignment)
        data=asdict(assignment); data.update(status=str(assignment.status), prestashop_status=str(assignment.prestashop_status), shopcaisse_status=str(assignment.shopcaisse_status))
        with self.db: self.db.execute("INSERT OR REPLACE INTO barcode_assignments VALUES(?,?)", (assignment.id, json.dumps(data)))

    def add_activity(self, activity: ActivityLog) -> None:
        super().add_activity(activity)
        with self.db: self.db.execute("INSERT OR REPLACE INTO activity_logs VALUES(?,?,?)", (activity.id, activity.timestamp, json.dumps(asdict(activity))))
