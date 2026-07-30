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


class MovementStatus(StrEnum):
    """Lifecycle states for the append-only stock ledger.

    This PR only creates ``PENDING`` movements.  Later workflows may validate,
    apply, fail or cancel them without changing their immutable business payload.
    """

    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductStatus(StrEnum):
    """Minimal product lifecycle: usable, paused, or history-only."""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class SupplierStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass
class Supplier:
    """Canonical supplier, independent from every external connector."""
    supplier_id: str
    name: str
    status: SupplierStatus = SupplierStatus.ACTIVE
    email: str = ""
    phone: str = ""
    website: str = ""
    address: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    contact_name: str = ""
    notes: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.supplier_id.strip():
            raise ValueError("supplier_id is required")
        self.status = SupplierStatus(self.status)

    def transition_to(self, status: SupplierStatus) -> None:
        target = SupplierStatus(status)
        allowed = {
            SupplierStatus.ACTIVE: {SupplierStatus.INACTIVE, SupplierStatus.ARCHIVED},
            SupplierStatus.INACTIVE: {SupplierStatus.ACTIVE, SupplierStatus.ARCHIVED},
            SupplierStatus.ARCHIVED: {SupplierStatus.INACTIVE},
        }
        if target != self.status and target not in allowed[self.status]:
            raise ValueError(f"transition {self.status} -> {target} is forbidden")
        if target != self.status:
            self.status = target
            self.updated_at = utc_now()


class CatalogueCoherence(StrEnum):
    OK = "OK"
    WARNING = "ATTENTION"
    INCONSISTENT = "INCOHÉRENT"


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
    reference: str = ""
    status: ProductStatus = ProductStatus.ACTIVE
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        """External references may change; the DrCloud key never does."""
        for field_name in ("drcloud_product_key", "prestashop_key", "shopcaisse_item_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        self.status = ProductStatus(self.status)

    def transition_to(self, status: ProductStatus) -> None:
        """Apply the deliberately small, reversible lifecycle state machine."""
        target = ProductStatus(status)
        allowed = {
            ProductStatus.ACTIVE: {ProductStatus.INACTIVE, ProductStatus.ARCHIVED},
            ProductStatus.INACTIVE: {ProductStatus.ACTIVE, ProductStatus.ARCHIVED},
            ProductStatus.ARCHIVED: {ProductStatus.INACTIVE},
        }
        if target != self.status and target not in allowed[self.status]:
            raise ValueError(f"transition {self.status} -> {target} is forbidden")
        self.status = target
        self.updated_at = utc_now()


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
    source_type: str
    source_id: str | None
    idempotency_key: str
    status: MovementStatus = MovementStatus.PENDING
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    validated_at: str | None = None
    applied_at: str | None = None
    actor: str | None = None
    result_message: str | None = None

    def __post_init__(self) -> None:
        if not self.drcloud_product_key.strip():
            raise ValueError("drcloud_product_key is required")
        if isinstance(self.quantity_delta, bool) or not isinstance(self.quantity_delta, int) or self.quantity_delta == 0:
            raise ValueError("quantity_delta must be a non-zero integer")
        if not self.source_type.strip():
            raise ValueError("source_type is required")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")

    def business_payload(self) -> tuple[str, int, MovementType, str, str | None]:
        """Fields that must match when an idempotency key is replayed."""
        return (self.drcloud_product_key, self.quantity_delta, self.movement_type,
                self.source_type, self.source_id)


class StockCoherence(StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    INCONSISTENT = "INCONSISTENT"


class ObservationFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ComparisonStatus(StrEnum):
    MATCH = "MATCH"
    DIFFERENCE = "DIFFERENCE"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    INCONSISTENT = "INCONSISTENT"


@dataclass(frozen=True)
class ExternalStockObservation:
    """A dated, read-only reference; never a local stock authority."""
    drcloud_product_key: str
    source: str
    quantity: int
    observed_at: str
    job_id: str
    freshness: ObservationFreshness


@dataclass(frozen=True)
class StockPosition:
    """Read-only position reconstructed from applied ledger movements."""
    drcloud_product_key: str
    reference: str
    name: str
    quantity: int
    last_movement_at: str
    last_source_type: str
    coherence: StockCoherence = StockCoherence.OK
    issue: str | None = None
