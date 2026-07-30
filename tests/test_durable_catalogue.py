import json
import sqlite3

import pytest

from dr_cloud_sync.domain import Product, ProductStatus, StockMovement, MovementType, MovementStatus
from dr_cloud_sync.inventory import InventoryRepository, InventoryService
from dr_cloud_sync.repositories import SQLiteOSRepository


def product(key="1", *, ean="4006381333931"):
    return Product(f"drc:p:{key}", f"p:{key}", key, "0", f"sc-{key}", f"Produit {key}", ean, reference=f"REF-{key}")


def test_product_identity_and_lifecycle_are_explicit():
    item=product(); identity=item.drcloud_product_key
    item.name="Nouveau nom"; item.reference="NEW"; item.transition_to(ProductStatus.INACTIVE)
    assert item.drcloud_product_key == identity and item.status == ProductStatus.INACTIVE
    item.transition_to(ProductStatus.ARCHIVED)
    with pytest.raises(ValueError): item.transition_to(ProductStatus.ACTIVE)
    item.transition_to(ProductStatus.INACTIVE); item.transition_to(ProductStatus.ACTIVE)


def test_sqlite_bootstrap_is_idempotent_and_does_not_overwrite(tmp_path):
    path=tmp_path/"catalogue.sqlite"
    first=SQLiteOSRepository(path,[product()]); first.get("drc:p:1").name="mémoire"
    first.db.execute("UPDATE drcloud_products SET name='Nom durable' WHERE drcloud_product_key='drc:p:1'"); first.db.commit(); first.db.close()
    second=SQLiteOSRepository(path,[Product("drc:p:1","p:changed",99,0,"changed","Source modifiée")])
    stored=second.get("drc:p:1")
    assert stored.name == "Nom durable" and stored.prestashop_key == "p:1"
    assert len(second.all()) == 1


def test_status_ean_and_external_reference_invariants_survive_reconnect(tmp_path):
    path=tmp_path/"catalogue.sqlite"; repo=SQLiteOSRepository(path,[product("1"),product("2",ean="")])
    repo.set_status("drc:p:1",ProductStatus.ARCHIVED); repo.db.close()
    reopened=SQLiteOSRepository(path,[])
    assert reopened.get("drc:p:1").status == ProductStatus.ARCHIVED
    with pytest.raises(ValueError): reopened.set_ean("drc:p:2","4006381333931")
    with pytest.raises(ValueError): SQLiteOSRepository(tmp_path/"conflict.sqlite",[product("1"),Product("drc:other","p:1","1","0","sc-other","Other","")])


def test_archived_product_remains_resolvable_for_stock_history(tmp_path):
    path=tmp_path/"catalogue.sqlite"; repo=SQLiteOSRepository(path,[product()]); repo.set_status("drc:p:1",ProductStatus.ARCHIVED)
    db=sqlite3.connect(path); db.execute("CREATE TABLE IF NOT EXISTS proof(value TEXT)"); db.close()
    assert repo.get("drc:p:1").name == "Produit 1"
    assert repo.activities()[-1].event_type == "PRODUCT_STATUS_CHANGED"


def test_inventory_accepts_a_valid_variable_catalogue(tmp_path):
    rows=[{"prestashop_key":f"p:{i}","product_id":i,"combination_id":0,"name":f"P{i}","ean":"",
           "shopcaisse_item_id":f"sc-{i}"} for i in range(3)]
    source=tmp_path/"catalogue.json"; source.write_text(json.dumps({"mappings":rows}))
    report=tmp_path/"report.json"; report.write_text(json.dumps({"ready_for_inventory":True}))
    service=InventoryService(source,report,InventoryRepository(tmp_path/"inventory.sqlite"))
    assert service.progress() == {"counted":0,"remaining":3,"total":3,"percent":0.0}
