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


class MediaRole(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class MediaSource(StrEnum):
    PRESTASHOP = "PRESTASHOP"
    MANUAL_UPLOAD = "MANUAL_UPLOAD"
    MOBILE_CAMERA = "MOBILE_CAMERA"
    IMPORT = "IMPORT"
    AI_GENERATED = "AI_GENERATED"


class MarketingUsage(StrEnum):
    UNKNOWN = "UNKNOWN"
    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"


class VisualType(StrEnum):
    UNSPECIFIED = "UNSPECIFIED"
    PACKSHOT = "PACKSHOT"
    PRODUCT_PHOTO = "PRODUCT_PHOTO"
    LIFESTYLE = "LIFESTYLE"
    PACKAGING = "PACKAGING"
    OTHER = "OTHER"


class MediaVariantKind(StrEnum):
    ORIGINAL = "ORIGINAL"
    THUMBNAIL = "THUMBNAIL"
    DISPLAY = "DISPLAY"


@dataclass(frozen=True)
class ProductMedia:
    media_id: str
    product_key: str
    media_type: str
    role: MediaRole
    source: MediaSource
    storage_reference: str
    mime_type: str
    width: int
    height: int
    file_size: int
    sha256: str
    source_reference: str | None = None
    original_filename: str | None = None
    visual_type: VisualType = VisualType.UNSPECIFIED
    marketing_usage: MarketingUsage = MarketingUsage.UNKNOWN
    protected_original: bool = False
    usages: tuple[str, ...] = ("catalogue",)
    imported_at: str | None = None
    source_updated_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    active: bool = True

    def __post_init__(self) -> None:
        if not self.media_id.startswith("media:") or not self.product_key.strip():
            raise ValueError("invalid product media identity")
        object.__setattr__(self, "role", MediaRole(self.role))
        object.__setattr__(self, "source", MediaSource(self.source))
        object.__setattr__(self, "visual_type", VisualType(self.visual_type))
        object.__setattr__(self, "marketing_usage", MarketingUsage(self.marketing_usage))
        allowed = {"catalogue", "ecommerce", "marketing", "social"}
        if not set(self.usages).issubset(allowed):
            raise ValueError("invalid media usage")


@dataclass(frozen=True)
class ProductMediaVariant:
    media_id: str
    kind: MediaVariantKind
    storage_reference: str
    mime_type: str
    width: int
    height: int
    file_size: int
    sha256: str


class SupplierStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    ORDERED = "ORDERED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"

class GoodsReceiptStatus(StrEnum):
    DRAFT = "DRAFT"
    APPLIED = "APPLIED"

@dataclass(frozen=True)
class GoodsReceiptLine:
    receipt_line_id: str
    receipt_id: str
    purchase_order_line_id: str
    product_key: str
    received_quantity: int

    def __post_init__(self) -> None:
        if not self.receipt_line_id.startswith("grl:") or not self.receipt_id.startswith("gr:"):
            raise ValueError("invalid goods receipt line identity")
        if not self.purchase_order_line_id.startswith("pol:") or not self.product_key.strip():
            raise ValueError("invalid purchase order line or product")
        if isinstance(self.received_quantity, bool) or not isinstance(self.received_quantity, int) or self.received_quantity <= 0:
            raise ValueError("received_quantity must be a positive integer")

@dataclass(frozen=True)
class GoodsReceipt:
    receipt_id: str
    purchase_order_id: str
    status: GoodsReceiptStatus = GoodsReceiptStatus.DRAFT
    received_at: str = field(default_factory=utc_now)
    received_by: str = "authenticated"
    notes: str = ""
    created_at: str = field(default_factory=utc_now)
    applied_at: str | None = None
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.receipt_id.startswith("gr:") or not self.purchase_order_id.startswith("po:"):
            raise ValueError("invalid goods receipt identity")
        object.__setattr__(self, "status", GoodsReceiptStatus(self.status))


@dataclass(frozen=True)
class PurchaseOrderLine:
    line_id: str
    purchase_order_id: str
    product_key: str
    ordered_quantity: int
    supplier_product_reference: str = ""
    unit_cost: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    discount: str = "0.00"

    def __post_init__(self) -> None:
        if not self.line_id.startswith("pol:") or not self.purchase_order_id.startswith("po:"):
            raise ValueError("invalid purchase order line identity")
        if not self.product_key.strip():
            raise ValueError("product_key is required")
        if isinstance(self.ordered_quantity, bool) or not isinstance(self.ordered_quantity, int) or self.ordered_quantity <= 0:
            raise ValueError("ordered_quantity must be a positive integer")


@dataclass(frozen=True)
class PurchaseOrder:
    purchase_order_id: str
    supplier_id: str
    reference: str
    status: PurchaseOrderStatus = PurchaseOrderStatus.DRAFT
    supplier_reference: str = ""
    ordered_at: str | None = None
    expected_at: str | None = None
    notes: str = ""
    currency: str = "EUR"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    fees: str = "0.00"

    def __post_init__(self) -> None:
        if not self.purchase_order_id.startswith("po:"):
            raise ValueError("invalid purchase_order_id")
        if not self.supplier_id.strip() or not self.reference.strip():
            raise ValueError("supplier_id and reference are required")
        object.__setattr__(self, "status", PurchaseOrderStatus(self.status))


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
    currency: str = "EUR"
    minimum_order: str | None = None
    fees: str | None = None

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
    base_name: str = ""
    variant_name: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    name_source: str = "DRCLOUD"
    variant_source: str = ""
    reference_source: str = ""
    ean_source: str = ""

    def __post_init__(self) -> None:
        """External references may change; the DrCloud key never does."""
        for field_name in ("drcloud_product_key", "prestashop_key", "shopcaisse_item_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        self.status = ProductStatus(self.status)
        self.base_name = self.base_name.strip() or self.name.strip()
        self.variant_name = self.variant_name.strip()
        self.attributes = {str(k).strip(): str(v).strip() for k, v in self.attributes.items()
                           if str(k).strip() and str(v).strip()}

    @property
    def display_name(self) -> str:
        """Central commercial label; it is deliberately unrelated to identity."""
        return f"{self.base_name} — {self.variant_name}" if self.variant_name else self.base_name

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
