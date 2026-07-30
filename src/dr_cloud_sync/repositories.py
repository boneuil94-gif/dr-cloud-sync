"""Persistence boundaries and the replaceable SQLite DrCloud OS adapter."""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from typing import Protocol

from .domain import (ActivityLog, AssignmentStatus, BarcodeAssignment, MovementStatus,
                     MovementType, Product, ProductStatus, RemoteStatus, StockMovement,
                     utc_now)


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
    def current_quantity(self, product_id: str) -> int: ...
    def current_positions(self) -> list[dict]: ...
    def recent_movements(self, limit: int = 50) -> list[StockMovement]: ...
    def movements_for_product(self, product_id: str) -> list[StockMovement]: ...
    def aggregate_statistics(self) -> dict[str, int]: ...


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

    def current_quantity(self, product_id: str) -> int:
        row=self.db.execute("SELECT COALESCE(SUM(quantity_delta),0) FROM stock_movements WHERE drcloud_product_key=? AND status='APPLIED'",(product_id,)).fetchone()
        return int(row[0])

    def current_positions(self) -> list[dict]:
        rows=self.db.execute("""WITH ranked AS (SELECT drcloud_product_key,source_type,
          COALESCE(applied_at,created_at) occurred_at,
          ROW_NUMBER() OVER (PARTITION BY drcloud_product_key ORDER BY COALESCE(applied_at,created_at) DESC,created_at DESC,id DESC) rank
          FROM stock_movements WHERE status='APPLIED'), totals AS
          (SELECT drcloud_product_key,SUM(quantity_delta) quantity,MAX(COALESCE(applied_at,created_at)) last_movement_at
           FROM stock_movements WHERE status='APPLIED' GROUP BY drcloud_product_key)
          SELECT totals.*,ranked.source_type last_source_type FROM totals JOIN ranked USING(drcloud_product_key)
          WHERE ranked.rank=1 ORDER BY totals.drcloud_product_key""").fetchall()
        return [dict(row) for row in rows]

    def recent_movements(self, limit: int = 50) -> list[StockMovement]:
        limit=max(0,min(int(limit),200))
        return [self._movement(r) for r in self.db.execute("SELECT * FROM stock_movements WHERE status='APPLIED' ORDER BY COALESCE(applied_at,created_at) DESC,id DESC LIMIT ?",(limit,))]  # type: ignore[misc]

    def movements_for_product(self, product_id: str) -> list[StockMovement]:
        return [self._movement(r) for r in self.db.execute("SELECT * FROM stock_movements WHERE drcloud_product_key=? AND status='APPLIED' ORDER BY COALESCE(applied_at,created_at) DESC,id DESC",(product_id,))]  # type: ignore[misc]

    def aggregate_statistics(self) -> dict[str, int]:
        row=self.db.execute("""SELECT COUNT(DISTINCT drcloud_product_key),
          COALESCE(SUM(quantity_delta),0), COUNT(*),
          COALESCE(SUM(CASE WHEN date(COALESCE(applied_at,created_at))=date('now') THEN 1 ELSE 0 END),0)
          FROM stock_movements WHERE status='APPLIED'""").fetchone()
        negative=self.db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM stock_movements WHERE status='APPLIED' GROUP BY drcloud_product_key HAVING SUM(quantity_delta)<0)").fetchone()[0]
        return {"products_tracked":row[0],"total_units":row[1],"movements":row[2],"movements_today":row[3],"negative_positions":negative}


class CatalogRepository(Protocol):
    def all(self) -> list[Product]: ...
    def get(self, key: str) -> Product | None: ...
    def by_ean(self, ean: str) -> list[Product]: ...
    def set_ean(self, key: str, ean: str) -> None: ...
    def set_status(self, key: str, status: ProductStatus) -> Product: ...


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
    def set_status(self, key: str, status: ProductStatus) -> Product:
        product=self.products[key]; product.transition_to(status); return product


class MemoryAuditRepository:
    def __init__(self): self.assignments: dict[str, BarcodeAssignment] = {}; self.logs: list[ActivityLog] = []
    def save_assignment(self, assignment: BarcodeAssignment) -> None: self.assignments[assignment.id] = assignment
    def assignment(self, identifier: str) -> BarcodeAssignment | None: return self.assignments.get(identifier)
    def add_activity(self, activity: ActivityLog) -> None: self.logs.append(activity)
    def activities(self) -> list[ActivityLog]: return list(self.logs)


class SQLiteOSRepository(MemoryCatalogRepository, MemoryAuditRepository):
    """Durable catalogue and audit adapter.

    ``products`` is bootstrap input only: existing rows are never overwritten.
    Additive columns also upgrade the former JSON-only ``drcloud_products`` table.
    """
    def __init__(self, path: Path, products: list[Product]):
        MemoryCatalogRepository.__init__(self, products)
        MemoryAuditRepository.__init__(self)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS barcode_assignments(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_logs(id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalogue_eans(drcloud_product_key TEXT PRIMARY KEY, ean TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS drcloud_products(
          drcloud_product_key TEXT PRIMARY KEY, data TEXT, updated_at TEXT,
          prestashop_key TEXT, product_id TEXT, combination_id TEXT,
          shopcaisse_item_id TEXT, name TEXT, ean TEXT NOT NULL DEFAULT '',
          reference TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ACTIVE',
          created_at TEXT);
        """)
        columns={r[1] for r in self.db.execute("PRAGMA table_info(drcloud_products)")}
        declarations={"prestashop_key":"TEXT","product_id":"TEXT","combination_id":"TEXT",
          "shopcaisse_item_id":"TEXT","name":"TEXT","ean":"TEXT NOT NULL DEFAULT ''",
          "reference":"TEXT NOT NULL DEFAULT ''","status":"TEXT NOT NULL DEFAULT 'ACTIVE'",
          "created_at":"TEXT"}
        for name,declaration in declarations.items():
            if name not in columns: self.db.execute(f"ALTER TABLE drcloud_products ADD COLUMN {name} {declaration}")
        self._migrate_legacy_json()
        self._bootstrap(products)
        self._ensure_unique_references()
        loaded=[self._product(r) for r in self.db.execute("SELECT * FROM drcloud_products ORDER BY drcloud_product_key")]
        self.products={p.drcloud_product_key:p for p in loaded}

    def _migrate_legacy_json(self) -> None:
        for row in self.db.execute("SELECT * FROM drcloud_products WHERE prestashop_key IS NULL").fetchall():
            if not row["data"]: continue
            value=json.loads(row["data"]); stamp=row["updated_at"] or utc_now()
            self.db.execute("""UPDATE drcloud_products SET prestashop_key=?,product_id=?,combination_id=?,
              shopcaisse_item_id=?,name=?,ean=?,reference=?,status='ACTIVE',created_at=?,updated_at=?
              WHERE drcloud_product_key=?""",(value.get("prestashop_key"),value.get("product_id"),
              value.get("combination_id"),value.get("shopcaisse_item_id"),value.get("name") or value.get("nom complet") or "",
              value.get("ean") or value.get("EAN") or "",value.get("reference") or value.get("référence") or "",
              stamp,stamp,row["drcloud_product_key"]))
        self.db.commit()

    def _bootstrap(self, products: list[Product]) -> None:
        with self.db:
            for p in products:
                self.db.execute("""INSERT OR IGNORE INTO drcloud_products(
                  drcloud_product_key,prestashop_key,product_id,combination_id,shopcaisse_item_id,
                  name,ean,reference,status,created_at,updated_at,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (p.drcloud_product_key,p.prestashop_key,str(p.product_id),
                   None if p.combination_id is None else str(p.combination_id),str(p.shopcaisse_item_id),
                   p.name,p.ean,p.reference,p.status.value,p.created_at,p.updated_at,json.dumps(asdict(p),default=str)))

    def _ensure_unique_references(self) -> None:
        ean_conflicts=self.db.execute("SELECT ean FROM drcloud_products WHERE ean!='' GROUP BY ean HAVING COUNT(*)>1").fetchall()
        external_conflicts=[]
        for fields in ("prestashop_key,product_id,COALESCE(combination_id,'')", "shopcaisse_item_id"):
            rows=self.db.execute(f"SELECT {fields},COUNT(*) n FROM drcloud_products WHERE status!='ARCHIVED' AND {fields.split(',')[0]} IS NOT NULL AND {fields.split(',')[0]}!='' GROUP BY {fields} HAVING n>1").fetchall()
            external_conflicts.extend(rows)
        if external_conflicts:
            raise ValueError("Catalogue incohérent: références externes dupliquées; migration annulée")
        with self.db:
            # Historical duplicates are preserved and surfaced by diagnostics.
            # Once cleaned explicitly, the next idempotent startup adds the constraint.
            if not ean_conflicts:
                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_products_ean ON drcloud_products(ean) WHERE ean!=''")
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_products_prestashop ON drcloud_products(prestashop_key,product_id,COALESCE(combination_id,'')) WHERE status!='ARCHIVED'")
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_products_shopcaisse ON drcloud_products(shopcaisse_item_id) WHERE status!='ARCHIVED'")

    @staticmethod
    def _product(row: sqlite3.Row) -> Product:
        return Product(row["drcloud_product_key"],row["prestashop_key"],row["product_id"],row["combination_id"],
          row["shopcaisse_item_id"],row["name"] or "",row["ean"] or "",reference=row["reference"] or "",
          status=ProductStatus(row["status"]),created_at=row["created_at"] or row["updated_at"] or utc_now(),
          updated_at=row["updated_at"] or utc_now())

    def set_ean(self, key: str, ean: str) -> None:
        duplicate=self.db.execute("SELECT drcloud_product_key FROM drcloud_products WHERE ean=? AND drcloud_product_key!=?",(ean,key)).fetchone() if ean else None
        if duplicate: raise ValueError("EAN déjà associé à un autre produit")
        try:
            with self.db:
                self.db.execute("UPDATE drcloud_products SET ean=?,updated_at=? WHERE drcloud_product_key=?",(ean,utc_now(),key))
                self.db.execute("INSERT OR REPLACE INTO catalogue_eans VALUES(?,?)", (key, ean))
        except sqlite3.IntegrityError as exc: raise ValueError("EAN déjà associé à un autre produit") from exc
        super().set_ean(key, ean)

    def set_status(self, key: str, status: ProductStatus) -> Product:
        product=self.get(key)
        if product is None: raise KeyError(key)
        old=product.status; product.transition_to(status)
        try:
            with self.db: self.db.execute("UPDATE drcloud_products SET status=?,updated_at=? WHERE drcloud_product_key=?",(product.status.value,product.updated_at,key))
        except sqlite3.IntegrityError:
            product.status=old
            raise ValueError("Référence externe déjà utilisée par un produit exploitable")
        self.add_activity(ActivityLog("PRODUCT_STATUS_CHANGED",key,"CATALOGUE",{"from":old.value,"to":product.status.value}))
        return product

    def save_assignment(self, assignment: BarcodeAssignment) -> None:
        super().save_assignment(assignment)
        data=asdict(assignment); data.update(status=str(assignment.status), prestashop_status=str(assignment.prestashop_status), shopcaisse_status=str(assignment.shopcaisse_status))
        with self.db: self.db.execute("INSERT OR REPLACE INTO barcode_assignments VALUES(?,?)", (assignment.id, json.dumps(data)))

    def add_activity(self, activity: ActivityLog) -> None:
        super().add_activity(activity)
        with self.db: self.db.execute("INSERT OR REPLACE INTO activity_logs VALUES(?,?,?)", (activity.id, activity.timestamp, json.dumps(asdict(activity))))
