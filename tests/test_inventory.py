import csv
import io
import json
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from dr_cloud_sync.inventory import InventoryError, InventoryRepository, InventoryService
from dr_cloud_sync.inventory_web import InventoryApp
from frontend_assets import assert_no_frontend_secrets


@pytest.fixture
def service(tmp_path):
    rows=[]
    for i in range(478):
        rows.append({"prestashop_key":f"p:{i}","product_id":i,"combination_id":i or 0,
          "name":f"Cloud produit {i}","ean":"DUP" if i in (1,2) else "" if i==3 else f"EAN{i}",
          "reference":f"REF{i}","shopcaisse_item_id":f"sc-{i}","stock_prestashop":2,"stock_shopcaisse":3})
    catalogue=tmp_path/"mapping.json"; catalogue.write_text(json.dumps({"mappings":rows}))
    report=tmp_path/"report.json"; report.write_text(json.dumps({"ready_for_inventory":True}))
    return InventoryService(catalogue,report,InventoryRepository(tmp_path/"inventory.sqlite3"))


def test_catalogue_scan_and_search(service):
    assert len(service.items)==478
    assert service.scan("EAN0")["items"][0]["prestashop_key"]=="p:0"
    assert service.scan("UNKNOWN")=={"status":"UNKNOWN","items":[]}
    assert service.scan("DUP")["status"]=="AMBIGUOUS"
    assert service.search("produit 12")[0]["name"]=="Cloud produit 12"
    assert service.search("REF22")[0]["reference"]=="REF22"
    assert service.search("p:32")[0]["prestashop_key"]=="p:32"
    assert [x["prestashop_key"] for x in service.search(without_ean=True)]==["p:3"]


def test_quantity_zero_increment_edit_and_history(service):
    saved=service.count("p:0",0,"MANUAL")
    assert saved["counted"]==1 and saved["physical_quantity"]==0
    assert service.progress()["counted"]==1
    assert service.count("p:0",None,"SCAN","INCREMENT")["physical_quantity"]==1
    assert service.count("p:0",None,"SCAN","INCREMENT")["physical_quantity"]==2
    assert service.count("p:0",5,"MANUAL","EDIT")["physical_quantity"]==5
    assert service.count("p:0",None,"MANUAL","DECREMENT")["physical_quantity"]==4
    assert service.count("p:0",None,"MANUAL","RESET")["physical_quantity"]==0
    assert [h["action"] for h in service.repo.history(service.session()["id"])]==["COUNT","INCREMENT","INCREMENT","EDIT","DECREMENT","RESET"]
    with pytest.raises(InventoryError): service.count("p:1",-1,"MANUAL")
    with pytest.raises(InventoryError): service.count("absent",1,"MANUAL")


def test_persistence_views_and_completion(service):
    service.count("p:0",0,"SEARCH")
    again=InventoryService(service_path(service,"mapping.json"),service_path(service,"report.json"),InventoryRepository(service.repo.path))
    assert again.progress()=={"counted":1,"remaining":477,"total":478,"percent":0.2}
    assert len(again.search(view="COUNTED"))==1 and len(again.search(view="REMAINING"))==477
    assert again.complete()["completed"] is False
    for i in range(1,478): again.count(f"p:{i}",i%4,"MANUAL")
    assert again.progress()["counted"]==478
    assert again.complete()["completed"] is True
    report=again.report(); assert report["ready_for_stock_correction"] is True and len(report["results"])==478
    assert report["physical_total_units"]==sum(i%4 for i in range(1,478))


def service_path(service,name): return service.repo.path.parent/name


def test_csv_columns_and_differences(service):
    service.count("p:0",4,"MANUAL")
    row=service.results()[0]
    assert row["difference_prestashop"]==2 and row["difference_shopcaisse"]==1
    parsed=list(csv.DictReader(io.StringIO(service.csv())))
    assert len(parsed)==478 and list(parsed[0])==["prestashop_key","product_id","combination_id","name","ean","reference","shopcaisse_item_id","physical_quantity","stock_prestashop","stock_shopcaisse","difference_prestashop","difference_shopcaisse","counted_at"]


def request(app,path,method="GET",body=None):
    env={};setup_testing_defaults(env);parts=path.split("?",1);env["PATH_INFO"]=parts[0];env["QUERY_STRING"]=parts[1] if len(parts)>1 else "";env["REQUEST_METHOD"]=method
    if body is not None:
        raw=json.dumps(body).encode();env["wsgi.input"]=io.BytesIO(raw);env["CONTENT_LENGTH"]=str(len(raw))
    status=[]; data=b"".join(app(env,lambda s,h:status.append(s)))
    return status[0],data


def test_web_api_and_no_secrets(service):
    app=InventoryApp(service)
    assert request(app,"/")[0]=="200 OK"
    assert json.loads(request(app,"/api/scan?ean=EAN0")[1])["status"]=="UNIQUE"
    status,data=request(app,"/api/count","POST",{"prestashop_key":"p:0","physical_quantity":0,"source":"SCAN"})
    assert status=="200 OK" and json.loads(data)["counted"]==1
    assert request(app,"/api/export.csv")[0]=="200 OK"
    assert_no_frontend_secrets(
        Path(__file__).parents[1] / "src/dr_cloud_sync/static",
        ("API_KEY", "PRESTASHOP_API_KEY"),
    )


def test_invalid_mapping_is_refused(tmp_path):
    cat=tmp_path/"c";cat.write_text("[]");rep=tmp_path/"r";rep.write_text('{"ready_for_inventory":false}')
    with pytest.raises(InventoryError): InventoryService(cat,rep,InventoryRepository(tmp_path/"d"))
