"""Pure DrCloud OS domain types (no database, HTTP, or vendor dependency)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def drcloud_key(prestashop_key: str) -> str:
    """Return the permanent identity for an existing mapped PrestaShop item."""
    if not prestashop_key:
        raise ValueError("prestashop_key is required")
    return f"drc:{prestashop_key}"


class AssignmentStatus(StrEnum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    SYNCING = "SYNCING"
    SYNC_PENDING = "SYNC_PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


class RemoteStatus(StrEnum):
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"
    OK = "OK"
    FAILED = "FAILED"


class MovementType(StrEnum):
    INVENTORY_CORRECTION = "INVENTORY_CORRECTION"
    SUPPLIER_RECEIPT = "SUPPLIER_RECEIPT"
    SALE = "SALE"
    RETURN = "RETURN"
    LOSS = "LOSS"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"


@dataclass
class Product:
    drcloud_product_key: str
    prestashop_key: str
    product_id: int | str
    combination_id: int | str | None
    shopcaisse_item_id: int | str
    name: str
    ean: str = ""
    physical_quantity: int | None = None
    stock_prestashop: int | None = None
    stock_shopcaisse: int | None = None


@dataclass
class BarcodeAssignment:
    drcloud_product_key: str
    ean: str
    previous_ean: str = ""
    status: AssignmentStatus = AssignmentStatus.PENDING_CONFIRMATION
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    confirmed_at: str | None = None
    prestashop_status: RemoteStatus = RemoteStatus.PENDING
    shopcaisse_status: RemoteStatus = RemoteStatus.PENDING
    completed_at: str | None = None
    error: str | None = None
    payloads: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActivityLog:
    event_type: str
    drcloud_product_key: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class StockMovement:
    drcloud_product_key: str
    quantity_delta: int
    movement_type: MovementType
    source_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    validated_at: str | None = None
