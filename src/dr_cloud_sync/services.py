"""Reusable DrCloud OS use cases."""
from __future__ import annotations
import os
from dataclasses import asdict

from .connectors import BarcodeConnector, prestashop_barcode_target, shopcaisse_barcode_target
from .domain import ActivityLog, AssignmentStatus, BarcodeAssignment, MovementType, Product, RemoteStatus, StockMovement, utc_now
from .repositories import AuditRepository, CatalogRepository


class BarcodeError(ValueError): pass


def validate_ean(value: str) -> str:
    ean=value.strip()
    if not ean or not ean.isdecimal() or len(ean) not in (8, 13): raise BarcodeError("EAN invalide (EAN-8 ou EAN-13 requis)")
    digits=[int(x) for x in ean]
    expected=(10-sum((3 if (len(digits)-2-i)%2==0 else 1)*n for i,n in enumerate(digits[:-1]))%10)%10
    if digits[-1] != expected: raise BarcodeError("Checksum EAN invalide")
    return ean


class AssignBarcodeService:
    def __init__(self, catalog: CatalogRepository, audit: AuditRepository,
                 prestashop: BarcodeConnector, shopcaisse: BarcodeConnector, mode: str | None = None):
        self.catalog, self.audit = catalog, audit
        self.prestashop, self.shopcaisse = prestashop, shopcaisse
        self.mode = mode or os.environ.get("BARCODE_SYNC_MODE", "dry-run")
        if self.mode not in {"dry-run", "live"}: raise BarcodeError("BARCODE_SYNC_MODE invalide")

    def propose(self, key: str, value: str) -> BarcodeAssignment:
        product=self.catalog.get(key)
        if not product: raise BarcodeError("Produit DrCloud inconnu")
        ean=validate_ean(value); matches=self.catalog.by_ean(ean)
        assignment=BarcodeAssignment(key, ean, product.ean)
        if matches and matches[0].drcloud_product_key != key:
            assignment.status=AssignmentStatus.CONFLICT; assignment.error=f"EAN déjà associé à {matches[0].name}"
            self.audit.add_activity(ActivityLog("BARCODE_CONFLICT",key,"CATALOGUE",{"ean":ean,"existing_product":matches[0].drcloud_product_key}))
        elif matches:
            assignment.status=AssignmentStatus.COMPLETED; assignment.prestashop_status=assignment.shopcaisse_status=RemoteStatus.SKIPPED; assignment.completed_at=utc_now()
        self.audit.save_assignment(assignment); return assignment

    def confirm(self, identifier: str) -> BarcodeAssignment:
        assignment=self.audit.assignment(identifier)
        if not assignment: raise BarcodeError("Association inconnue")
        if assignment.status != AssignmentStatus.PENDING_CONFIRMATION: return assignment
        product=self.catalog.get(assignment.drcloud_product_key)
        if not product: raise BarcodeError("Produit DrCloud inconnu")
        assignment.confirmed_at=utc_now(); assignment.status=AssignmentStatus.SYNCING
        assignment.payloads={"prestashop":prestashop_barcode_target(product,assignment.ean),"shopcaisse":shopcaisse_barcode_target(product,assignment.ean)}
        if self.mode == "dry-run":
            assignment.prestashop_status=assignment.shopcaisse_status=RemoteStatus.SKIPPED
            assignment.status=AssignmentStatus.COMPLETED; assignment.completed_at=utc_now()
            self.catalog.set_ean(product.drcloud_product_key,assignment.ean)
        else: self._sync(assignment, product)
        self.audit.save_assignment(assignment)
        if assignment.status == AssignmentStatus.COMPLETED:
            self.audit.add_activity(ActivityLog("BARCODE_ASSIGNED",product.drcloud_product_key,"INVENTORY",{"ean":assignment.ean,"mode":self.mode}))
        return assignment

    def resume(self, identifier: str) -> BarcodeAssignment:
        assignment=self.audit.assignment(identifier)
        if not assignment or assignment.status != AssignmentStatus.SYNC_PENDING: raise BarcodeError("Aucune synchronisation en attente")
        product=self.catalog.get(assignment.drcloud_product_key)
        if not product: raise BarcodeError("Produit DrCloud inconnu")
        assignment.status=AssignmentStatus.SYNCING; assignment.error=None; self._sync(assignment,product); self.audit.save_assignment(assignment); return assignment

    def _sync(self, a: BarcodeAssignment, product: Product) -> None:
        try:
            if a.prestashop_status != RemoteStatus.OK: self.prestashop.write_and_verify(product,a.ean); a.prestashop_status=RemoteStatus.OK
            if a.shopcaisse_status != RemoteStatus.OK: self.shopcaisse.write_and_verify(product,a.ean); a.shopcaisse_status=RemoteStatus.OK
            self.catalog.set_ean(product.drcloud_product_key,a.ean); a.status=AssignmentStatus.COMPLETED; a.completed_at=utc_now()
        except Exception as exc:
            if a.prestashop_status != RemoteStatus.OK: a.prestashop_status=RemoteStatus.FAILED
            else: a.shopcaisse_status=RemoteStatus.FAILED
            a.status=AssignmentStatus.SYNC_PENDING if RemoteStatus.OK in (a.prestashop_status,a.shopcaisse_status) else AssignmentStatus.FAILED
            a.error=str(exc); self.audit.add_activity(ActivityLog("BARCODE_SYNC_FAILED",product.drcloud_product_key,"SYNC",{"ean":a.ean,"error":str(exc)}))


class InventoryReconciliationService:
    """Propose local movements only; it has no connector dependency."""
    def propose(self, products: list[Product], source_id: str) -> list[StockMovement]:
        return [StockMovement(p.drcloud_product_key,p.physical_quantity-p.stock_prestashop,MovementType.INVENTORY_CORRECTION,source_id)
                for p in products if p.physical_quantity is not None and p.stock_prestashop is not None and p.physical_quantity != p.stock_prestashop]
