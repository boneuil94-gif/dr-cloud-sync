import json
from dataclasses import replace
from pathlib import Path

from dr_cloud_sync.domain import MovementStatus, MovementType, Product, StockCoherence, StockMovement
from dr_cloud_sync.inventory import InventoryRepository, InventoryService
from dr_cloud_sync.inventory_web import InventoryApp
from dr_cloud_sync.repositories import MemoryCatalogRepository, SQLiteStockMovementRepository
from dr_cloud_sync.services import StockProjectionService, StockService
from test_inventory import complete_inventory, request


def applied(identifier="m1", product="drc:p:1", delta=3, created="2026-07-30T10:00:00+00:00", source="INVENTORY"):
    return StockMovement(id=identifier,drcloud_product_key=product,quantity_delta=delta,
      movement_type=MovementType.INVENTORY_CORRECTION,source_type=source,source_id="session-1",
      idempotency_key=f"key-{identifier}",status=MovementStatus.APPLIED,created_at=created,
      validated_at=created,applied_at=created,actor="alice")


def catalog():
    return MemoryCatalogRepository([Product("drc:p:1","p:1",1,0,"sc-1","Produit un",reference="REF-1")])


def test_empty_projection_and_statistics(tmp_path):
    repo=SQLiteStockMovementRepository(tmp_path/"stock.sqlite")
    assert StockProjectionService(repo,catalog()).positions()==[]
    assert repo.current_quantity("drc:p:1")==0
    assert repo.aggregate_statistics()=={"products_tracked":0,"total_units":0,"movements":0,"movements_today":0,"negative_positions":0}


def test_projection_reconstructs_applied_movements_deterministically(tmp_path):
    path=tmp_path/"stock.sqlite"; repo=SQLiteStockMovementRepository(path)
    repo.append(applied()); repo.append(applied("m2",delta=-3,created="2026-07-30T11:00:00+00:00",source="LEGACY"))
    # Pending entries are observations, not applicable ledger movements.
    repo.append(replace(applied("pending",delta=99),status=MovementStatus.PENDING,validated_at=None,applied_at=None))
    position=StockProjectionService(repo,catalog()).positions()[0]
    assert (position.quantity,position.reference,position.last_source_type,position.coherence)==(0,"REF-1","LEGACY",StockCoherence.OK)
    assert repo.current_quantity("drc:p:1")==0
    assert [m.id for m in repo.movements_for_product("drc:p:1")]==["m2","m1"]
    assert [m.id for m in repo.recent_movements()]==["m2","m1"]
    reopened=SQLiteStockMovementRepository(path)
    assert StockProjectionService(reopened,catalog()).positions()==[position]


def test_negative_and_orphan_positions_are_observable(tmp_path):
    repo=SQLiteStockMovementRepository(tmp_path/"stock.sqlite")
    repo.append(applied(delta=-2)); repo.append(applied("orphan","drc:missing",4))
    positions={p.drcloud_product_key:p for p in StockProjectionService(repo,catalog()).positions()}
    assert positions["drc:p:1"].coherence is StockCoherence.WARNING
    assert positions["drc:p:1"].issue=="Position négative"
    assert positions["drc:missing"].coherence is StockCoherence.INCONSISTENT
    assert repo.aggregate_statistics()["negative_positions"]==1


def make_service(tmp_path):
    rows=[{"prestashop_key":f"p:{i}","drcloud_product_key":f"drc:p:{i}","product_id":i,"combination_id":0,"name":f"Produit {i}","reference":f"REF{i}","ean":f"EAN{i}","shopcaisse_item_id":f"sc-{i}","stock_prestashop":2,"stock_shopcaisse":2} for i in range(478)]
    catalogue_path=tmp_path/"catalogue.json"; catalogue_path.write_text(json.dumps({"mappings":rows}))
    report=tmp_path/"report.json"; report.write_text('{"ready_for_inventory":true}')
    return InventoryService(catalogue_path,report,InventoryRepository(tmp_path/"app.sqlite"))


def test_inventory_to_stock_api_and_read_only_ui(tmp_path):
    service=make_service(tmp_path); complete_inventory(service,{0:5}); service.validate("alice"); service.apply("alice"); service.apply("alice")
    app=InventoryApp(service)
    status,payload=request(app,"/api/stock"); data=json.loads(payload)
    assert status=="200 OK" and data["statistics"]["products_tracked"]==1
    assert data["positions"][0]["quantity"]==3 and len(service.repo.list())==1
    detail=json.loads(request(app,"/api/stock/products/drc%3Ap%3A0")[1])
    assert detail["position"]["quantity"]==3 and detail["movements"][0]["origin"]=="Inventaire"
    html=request(app,"/stock")[1].decode()
    assert "Vue consolidée" in html and 'aria-current="page"' in html
    assert html.count("Stock</span><small>À venir") == 0
    assert "href=\"/achats\"" in html


def test_stock_module_requires_authentication(tmp_path):
    from dr_cloud_sync.os_config import OSSettings
    service=make_service(tmp_path)
    settings=OSSettings(environment="test",data_dir=tmp_path,admin_username="admin",admin_password="password-long",secret_key="x"*32,host="127.0.0.1",port=8080,safe_mode=True,trust_proxy=False)
    assert request(InventoryApp(service,settings=settings),"/stock")[0]=="303 See Other"
