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


def complete_inventory(service, quantities=None):
    quantities=quantities or {}
    for i in range(478):
        service.count(f"p:{i}",quantities.get(i,2),"MANUAL")
    return service.complete()


def test_inventory_workflow_persists_freezes_and_starts_fresh_session(service):
    assert service.session()["status"] == "IN_PROGRESS"
    assert service.complete()["completed"] is False
    completed=complete_inventory(service,{0:5,1:0})
    assert completed["completed"] is True
    assert service.session()["status"] == "PROPOSED"
    proposal=service.proposal()
    assert proposal and proposal["id"] == completed["proposal_id"]
    assert proposal["summary"] == {"lines":478,"increases":1,"decreases":1,"unchanged":476}
    assert service.create_proposal()["id"] == proposal["id"]
    with pytest.raises(InventoryError,match="gelée"):
        service.count("p:0",9,"MANUAL","EDIT")
    old_id=service.session()["id"]
    fresh=service.new_session()
    assert fresh["id"] != old_id and fresh["status"] == "IN_PROGRESS"
    assert service.repo.proposal(old_id)["id"] == proposal["id"]


def test_draft_starts_on_first_count(service):
    identifier=service.session()["id"]
    with service.repo.db:
        service.repo.db.execute("UPDATE sessions SET status='DRAFT',started_at=NULL WHERE id=?",(identifier,))
    service.count("p:0",1,"MANUAL")
    assert service.session()["status"] == "IN_PROGRESS"
    assert service.session()["started_at"] is not None


def test_human_validation_application_and_idempotent_replay(service):
    complete_inventory(service,{0:5,1:0})
    proposal=service.proposal(); assert proposal["status"] == "PROPOSED"
    with pytest.raises(InventoryError,match="validée"):
        service.apply("alice")
    validated=service.validate("alice")
    assert validated["status"] == "VALIDATED" and validated["actor"] == "alice"
    assert service.validate("bob")["actor"] == "alice"
    applied=service.apply("alice")
    assert applied["status"] == "APPLIED" and applied["movement_count"] == 2
    movements=service.repo.list()
    assert len(movements)==2 and all(m.status.value=="APPLIED" for m in movements)
    assert {m.idempotency_key for m in movements} == {
        f"inventory:{proposal['session_id']}:drc:p:0:v1",
        f"inventory:{proposal['session_id']}:drc:p:1:v1"}
    assert service.apply("alice")["movement_count"] == 2
    assert len(service.repo.list()) == 2


def test_application_rolls_back_and_retry_is_safe(service, monkeypatch):
    from dr_cloud_sync.services import StockService
    complete_inventory(service,{0:5,1:0}); service.validate("alice")
    original=StockService.apply; calls=0
    def fail_second(self,movement):
        nonlocal calls; calls+=1
        if calls == 2: raise RuntimeError("sqlite password=secret")
        return original(self,movement)
    monkeypatch.setattr(StockService,"apply",fail_second)
    with pytest.raises(InventoryError,match="transactionnelle"):
        service.apply("alice")
    assert service.repo.list() == []
    failed=service.proposal(); assert failed["status"] == "VALIDATED" and "secret" not in failed["error"]
    monkeypatch.setattr(StockService,"apply",original)
    retried=service.apply("alice")
    assert retried["status"] == "APPLIED" and len(service.repo.list()) == 2 and retried["error"] is None


def test_legacy_session_schema_is_migrated_without_data_loss(tmp_path):
    import sqlite3
    path=tmp_path/"legacy.sqlite3"; db=sqlite3.connect(path)
    db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,status TEXT NOT NULL CHECK(status IN ('DRAFT','IN_PROGRESS','COMPLETED','VALIDATED')))")
    db.execute("INSERT INTO sessions VALUES('old','2026-01-01','2026-01-01','2026-01-02','COMPLETED')"); db.commit(); db.close()
    repo=InventoryRepository(path)
    assert repo.db.execute("SELECT status FROM sessions WHERE id='old'").fetchone()[0] == "COMPLETED"
    assert {r[1] for r in repo.db.execute("PRAGMA table_info(inventory_stock_proposals)")} >= {"source_checksum","error","actor"}


def test_proposal_api_and_combined_csrf_action(service):
    complete_inventory(service,{0:5})
    app=InventoryApp(service)
    status,data=request(app,"/api/inventory/proposal")
    assert status=="200 OK" and json.loads(data)["status"]=="PROPOSED"
    status,data=request(app,"/api/inventory/proposal/validate-and-apply","POST")
    assert status=="200 OK" and json.loads(data)["status"]=="APPLIED"
    html=request(app,"/inventaire")[1].decode()
    assert "Proposition de mise à jour du stock" in html and "Valider et appliquer" in html
