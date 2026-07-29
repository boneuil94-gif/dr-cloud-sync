"""Persistence boundaries and the replaceable SQLite DrCloud OS adapter."""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from typing import Protocol

from .domain import ActivityLog, AssignmentStatus, BarcodeAssignment, Product, RemoteStatus


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
