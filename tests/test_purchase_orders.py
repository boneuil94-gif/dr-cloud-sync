import io
import json
import sqlite3
from decimal import Decimal

import pytest

from dr_cloud_sync.domain import Product, ProductStatus, PurchaseOrderStatus
from dr_cloud_sync.purchasing import (PurchaseOrderService, SQLitePurchaseOrderRepository,
                                      SQLiteSupplierRepository, SupplierService)
from dr_cloud_sync.repositories import MemoryAuditRepository, MemoryCatalogRepository
from dr_cloud_sync.inventory import InventoryRepository, InventoryService
from dr_cloud_sync.inventory_web import InventoryApp


def product(status=ProductStatus.ACTIVE):
    return Product("drc:1", "1", 1, None, "1", "Produit", "123", reference="REF", status=status)

@pytest.fixture
def services(tmp_path):
    path=tmp_path/"os.db"; audit=MemoryAuditRepository(); suppliers=SupplierService(SQLiteSupplierRepository(path),audit)
    catalogue=MemoryCatalogRepository([product()]); orders=PurchaseOrderService(SQLitePurchaseOrderRepository(path),suppliers,catalogue,audit)
    return suppliers,catalogue,orders,path,audit

def test_domain_workflow_identity_money_audit_and_immutability(services):
    suppliers,_,orders,_,audit=services; supplier,_=suppliers.create({"name":"Actif"})
    order=orders.create({"supplier_id":supplier.supplier_id,"notes":"test"})
    assert order.purchase_order_id.startswith("po:") and order.status is PurchaseOrderStatus.DRAFT
    with pytest.raises(ValueError,match="empty"): orders.transition(order.purchase_order_id,"ORDERED")
    line=orders.add_line(order.purchase_order_id,{"product_key":"drc:1","ordered_quantity":3,"unit_cost":"1.235"})
    assert line.line_id.startswith("pol:") and line.unit_cost=="1.24" and orders.total(order.purchase_order_id)=="3.72"
    changed=orders.update_line(order.purchase_order_id,line.line_id,{"ordered_quantity":2,"unit_cost":"2.00"})
    assert changed.ordered_quantity==2 and Decimal(orders.total(order.purchase_order_id))==Decimal("4.00")
    ordered=orders.transition(order.purchase_order_id,"ORDERED"); assert ordered.ordered_at
    assert orders.transition(order.purchase_order_id,"ORDERED")==ordered  # idempotent, no second audit
    with pytest.raises(ValueError,match="DRAFT"): orders.remove_line(order.purchase_order_id,line.line_id)
    with pytest.raises(ValueError,match="DRAFT"): orders.update(order.purchase_order_id,{"notes":"rewrite"})
    assert orders.transition(order.purchase_order_id,"CANCELLED").status is PurchaseOrderStatus.CANCELLED
    assert [a.event_type for a in audit.activities()].count("PURCHASE_ORDER_ORDERED")==1

def test_validation_supplier_product_quantity_cost_and_removal(services):
    suppliers,catalogue,orders,_,_=services
    with pytest.raises(ValueError,match="supplier"): orders.create({"supplier_id":"sup:missing"})
    supplier,_=suppliers.create({"name":"Archive"}); suppliers.transition(supplier.supplier_id,"ARCHIVED")
    with pytest.raises(ValueError,match="ACTIVE"): orders.create({"supplier_id":supplier.supplier_id})
    active,_=suppliers.create({"name":"Active"}); order=orders.create({"supplier_id":active.supplier_id})
    for quantity in (0,-1,"bad",1.5):
        with pytest.raises(ValueError,match="quantity"): orders.add_line(order.purchase_order_id,{"product_key":"drc:1","ordered_quantity":quantity})
    with pytest.raises(ValueError,match="product"): orders.add_line(order.purchase_order_id,{"product_key":"missing","ordered_quantity":1})
    catalogue.products["drc:1"].status=ProductStatus.ARCHIVED
    with pytest.raises(ValueError,match="ACTIVE"): orders.add_line(order.purchase_order_id,{"product_key":"drc:1","ordered_quantity":1})
    catalogue.products["drc:1"].status=ProductStatus.ACTIVE
    for cost in ("bad","-1","NaN"):
        with pytest.raises(ValueError,match="unit_cost"): orders.add_line(order.purchase_order_id,{"product_key":"drc:1","ordered_quantity":1,"unit_cost":cost})
    line=orders.add_line(order.purchase_order_id,{"product_key":"drc:1","ordered_quantity":1})
    assert orders.total(order.purchase_order_id) is None
    orders.remove_line(order.purchase_order_id,line.line_id); assert orders.lines(order.purchase_order_id)==[]

def test_sqlite_reconnect_additive_migration_and_history(services):
    suppliers,_,orders,path,_=services; supplier,_=suppliers.create({"name":"Persist"}); order=orders.create({"supplier_id":supplier.supplier_id})
    orders.add_line(order.purchase_order_id,{"product_key":"drc:1","ordered_quantity":2})
    orders.repository.db.close(); reopened=SQLitePurchaseOrderRepository(path)
    assert reopened.get(order.purchase_order_id).supplier_id==supplier.supplier_id and len(reopened.list_lines(order.purchase_order_id))==1
    assert sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE name='suppliers'").fetchone()
    SQLitePurchaseOrderRepository(path)  # idempotent migration

def request(app,path,method="GET",body=None,csrf="test"):
    raw=json.dumps(body or {}).encode(); status=[]; env={"PATH_INFO":path.split("?")[0],"QUERY_STRING":path.partition("?")[2],"REQUEST_METHOD":method,"wsgi.input":io.BytesIO(raw),"CONTENT_LENGTH":str(len(raw)),"HTTP_X_CSRF_TOKEN":csrf}
    payload=b"".join(app(env,lambda s,h:status.append(s))); return status[0],json.loads(payload) if payload and payload.startswith(b"{") else payload

@pytest.fixture
def app(tmp_path):
    catalogue=tmp_path/"catalogue.json"; catalogue.write_text(json.dumps([{"prestashop_key":"1","shopcaisse_item_id":"1","product_id":1,"combination_id":None,"name":"Test","reference":"REF"}]))
    validation=tmp_path/"validation.json"; validation.write_text('{"ready_for_inventory":true}')
    return InventoryApp(InventoryService(catalogue,validation,InventoryRepository(tmp_path/"app.db")))

def test_api_ui_and_absolute_no_stock_effect(app):
    _,s=request(app,"/api/suppliers","POST",{"name":"API"}); sid=s["supplier"]["supplier_id"]
    before_movements=app.service.repo.db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    before_positions=app.stock.positions()
    status,data=request(app,"/api/purchase-orders","POST",{"supplier_id":sid}); assert status=="201 Created"
    oid=data["purchase_order"]["purchase_order_id"]
    assert request(app,f"/api/purchase-orders/{oid}/status","POST",{"status":"ORDERED"})[0]=="400 Bad Request"
    status,line=request(app,f"/api/purchase-orders/{oid}/lines","POST",{"product_key":"drc:1","ordered_quantity":2,"unit_cost":"4.50"}); assert status=="201 Created"
    assert request(app,f"/api/purchase-orders/{oid}/lines/{line['line']['line_id']}","PATCH",{"ordered_quantity":3})[0]=="200 OK"
    assert request(app,f"/api/purchase-orders/{oid}/status","POST",{"status":"ORDERED"})[1]["purchase_order"]["status"]=="ORDERED"
    assert request(app,f"/api/purchase-orders/{oid}/lines/{line['line']['line_id']}","DELETE")[0]=="400 Bad Request"
    assert app.service.repo.db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]==before_movements
    assert app.stock.positions()==before_positions
    assert request(app,"/api/purchase-orders/missing")[0]=="404 Not Found"
    page=request(app,"/achats")[1]; assert b"Commandes" in page and b"R\xc3\xa9ceptions" in page and b"R\xc3\xa9assort" in page

def test_purchase_order_api_auth_and_csrf(app):
    class Settings: secret_key="x"; environment="test"; safe_mode=True
    app.settings=Settings()
    assert request(app,"/api/purchase-orders")[0]=="303 See Other"

def test_goods_receipt_end_to_end_partial_complete_and_idempotent(app):
    _,supplier=request(app,"/api/suppliers","POST",{"name":"Engagement existant"})
    _,created=request(app,"/api/purchase-orders","POST",{"supplier_id":supplier["supplier"]["supplier_id"]})
    oid=created["purchase_order"]["purchase_order_id"]
    _,added=request(app,f"/api/purchase-orders/{oid}/lines","POST",{"product_key":"drc:1","ordered_quantity":10,"unit_cost":"2.50"})
    lid=added["line"]["line_id"]; request(app,f"/api/purchase-orders/{oid}/status","POST",{"status":"ORDERED"})
    _,draft=request(app,f"/api/purchase-orders/{oid}/receipts","POST",{"idempotency_key":"delivery-1","lines":[{"purchase_order_line_id":lid,"received_quantity":4}]})
    rid=draft["goods_receipt"]["receipt_id"]
    applied=request(app,f"/api/goods-receipts/{rid}/apply","POST"); assert applied[0]=="200 OK", applied; assert applied[1]["goods_receipt"]["status"]=="APPLIED"
    assert request(app,f"/api/purchase-orders/{oid}")[1]["purchase_order"]["status"]=="PARTIALLY_RECEIVED"
    assert app.service.repo.current_quantity("drc:1")==4
    # Applying the same stable receipt is a harmless replay.
    request(app,f"/api/goods-receipts/{rid}/apply","POST"); assert app.service.repo.current_quantity("drc:1")==4
    _,second=request(app,f"/api/purchase-orders/{oid}/receipts","POST",{"idempotency_key":"delivery-2","lines":[{"purchase_order_line_id":lid,"received_quantity":6}]})
    request(app,f"/api/goods-receipts/{second['goods_receipt']['receipt_id']}/apply","POST")
    assert request(app,f"/api/purchase-orders/{oid}")[1]["purchase_order"]["status"]=="RECEIVED"
    assert app.service.repo.current_quantity("drc:1")==10
    assert request(app,f"/api/purchase-orders/{oid}/receipts","POST",{"idempotency_key":"too-late","lines":[{"purchase_order_line_id":lid,"received_quantity":1}]})[0]=="400 Bad Request"
    detail=request(app,f"/api/goods-receipts/{rid}")[1]
    assert detail["movements"][0]["origin"]=="Réception fournisseur"

def test_goods_receipt_rejects_invalid_and_over_receipt(app):
    _,supplier=request(app,"/api/suppliers","POST",{"name":"Contrôles"})
    _,created=request(app,"/api/purchase-orders","POST",{"supplier_id":supplier["supplier"]["supplier_id"]}); oid=created["purchase_order"]["purchase_order_id"]
    _,added=request(app,f"/api/purchase-orders/{oid}/lines","POST",{"product_key":"drc:1","ordered_quantity":2}); lid=added["line"]["line_id"]
    request(app,f"/api/purchase-orders/{oid}/status","POST",{"status":"ORDERED"})
    for quantity in (0,-1,3):
        assert request(app,f"/api/purchase-orders/{oid}/receipts","POST",{"idempotency_key":f"bad-{quantity}","lines":[{"purchase_order_line_id":lid,"received_quantity":quantity}]})[0]=="400 Bad Request"
    request(app,f"/api/purchase-orders/{oid}/status","POST",{"status":"CANCELLED"})
    assert request(app,f"/api/purchase-orders/{oid}/receipts","POST",{"idempotency_key":"cancelled","lines":[{"purchase_order_line_id":lid,"received_quantity":1}]})[0]=="400 Bad Request"
