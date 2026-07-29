from dataclasses import replace
import pytest
from dr_cloud_sync.connectors import prestashop_barcode_target, shopcaisse_barcode_target
from dr_cloud_sync.domain import AssignmentStatus, MovementType, Product, RemoteStatus, drcloud_key
from dr_cloud_sync.repositories import MemoryAuditRepository, MemoryCatalogRepository
from dr_cloud_sync.services import AssignBarcodeService, BarcodeError, InventoryReconciliationService, validate_ean

class FakeConnector:
    def __init__(self, failures=0): self.calls=[]; self.failures=failures
    def write_and_verify(self, product, ean):
        self.calls.append((product.drcloud_product_key,ean))
        if self.failures: self.failures-=1; raise RuntimeError("remote failure")

def products():
    return [Product("drc:p:1","p:1",1,0,"sc-1","Simple","4006381333931",None,7,6),
            Product("drc:p:2","p:2",2,22,"sc-2","Déclinaison","",10,7,7)]

def setup(mode="dry-run", shop_failures=0):
    catalog=MemoryCatalogRepository(products()); audit=MemoryAuditRepository(); ps=FakeConnector(); sc=FakeConnector(shop_failures)
    return AssignBarcodeService(catalog,audit,ps,sc,mode),catalog,audit,ps,sc

def test_identity_is_deterministic_and_independent_of_mutable_fields():
    assert drcloud_key("p:42") == drcloud_key("p:42") == "drc:p:42"
    assert len({drcloud_key(f"p:{i}") for i in range(478)}) == 478

def test_ean_validation_and_known_lookup():
    assert validate_ean("4006381333931") == "4006381333931"
    assert validate_ean("96385074") == "96385074"
    for invalid in ("", "abc", "12345678", "4006381333932"):
        with pytest.raises(BarcodeError): validate_ean(invalid)

def test_unique_requires_confirmation_and_dry_run_writes_nothing():
    service,catalog,audit,ps,sc=setup(); assignment=service.propose("drc:p:2","96385074")
    assert assignment.status == AssignmentStatus.PENDING_CONFIRMATION
    assert not ps.calls and not sc.calls and catalog.get("drc:p:2").ean == ""
    done=service.confirm(assignment.id)
    assert done.status == AssignmentStatus.COMPLETED
    assert done.prestashop_status == done.shopcaisse_status == RemoteStatus.SKIPPED
    assert done.payloads["prestashop"] == {"resource":"combinations","id":22,"ean13":"96385074"}
    assert done.payloads["shopcaisse"] == {"shopcaisse_item_id":"sc-2","ean":"96385074"}
    assert not ps.calls and not sc.calls and audit.activities()[0].event_type == "BARCODE_ASSIGNED"

def test_same_product_no_write_and_other_product_conflict():
    service,_,audit,ps,sc=setup()
    same=service.propose("drc:p:1","4006381333931")
    assert same.status == AssignmentStatus.COMPLETED and not ps.calls and not sc.calls
    conflict=service.propose("drc:p:2","4006381333931")
    assert conflict.status == AssignmentStatus.CONFLICT
    assert audit.activities()[-1].event_type == "BARCODE_CONFLICT"

def test_targets_simple_combination_and_shopcaisse_exact_id():
    simple,combination=products()
    assert prestashop_barcode_target(simple,"x") == {"resource":"products","id":1,"ean13":"x"}
    assert prestashop_barcode_target(combination,"x")["resource"] == "combinations"
    assert "name" not in shopcaisse_barcode_target(combination,"x")

def test_partial_failure_is_resumable_without_double_prestashop_update():
    service,catalog,audit,ps,sc=setup("live",1); assignment=service.propose("drc:p:2","96385074"); pending=service.confirm(assignment.id)
    assert pending.status == AssignmentStatus.SYNC_PENDING and pending.prestashop_status == RemoteStatus.OK
    assert len(ps.calls)==len(sc.calls)==1 and catalog.get("drc:p:2").ean == ""
    completed=service.resume(assignment.id)
    assert completed.status == AssignmentStatus.COMPLETED and len(ps.calls)==1 and len(sc.calls)==2
    assert catalog.get("drc:p:2").ean == "96385074"
    assert any(x.event_type == "BARCODE_SYNC_FAILED" for x in audit.activities())

def test_reconciliation_only_proposes_local_deltas():
    rows=products(); rows[0].physical_quantity=10; rows[1].physical_quantity=7
    movements=InventoryReconciliationService().propose(rows,"inventory-1")
    assert [(m.drcloud_product_key,m.quantity_delta,m.movement_type) for m in movements] == [("drc:p:1",3,MovementType.INVENTORY_CORRECTION)]
