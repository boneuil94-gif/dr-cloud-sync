import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dr_cloud_sync.domain import (MarketingUsage, MediaRole, MediaSource, Product,
                                  ProductMedia, ProductStatus)
from dr_cloud_sync.marketing import (BrandKit, ConfigurableScoringEngine,
                                     MarketingAutopilot, MarketingRepository,
                                     UnavailableSalesMarketingData)
from dr_cloud_sync.media import SQLiteProductMediaRepository
from dr_cloud_sync.repositories import MemoryCatalogRepository
from test_os_production import configured, login, request  # noqa: F401


def foundation(tmp_path):
    key="drc:p:1"
    product=Product(key,"p:1",1,0,"sc-1","Hyper Max 50K — PEACH ICE","",status=ProductStatus.ACTIVE)
    catalogue=MemoryCatalogRepository([product]);media=SQLiteProductMediaRepository(tmp_path/"marketing.db")
    media.add(ProductMedia("media:1",key,"IMAGE",MediaRole.PRIMARY,MediaSource.PRESTASHOP,"products/one.png","image/png",800,800,10,"a"*64,marketing_usage=MarketingUsage.ALLOWED,protected_original=True,usages=("catalogue","marketing")),[])
    repository=MarketingRepository(tmp_path/"marketing.db")
    return repository,MarketingAutopilot(repository,catalogue,media),key


def test_preview_is_fact_based_and_has_no_mutation(tmp_path):
    repository,autopilot,key=foundation(tmp_path)
    preview=autopilot.preview()
    assert preview["mutated"] is False and preview["automation_enabled"] is False
    assert preview["candidates"][0]["product_key"]==key
    assert "Produit actif" in preview["candidates"][0]["opportunity"]["reason"]
    assert "sales" in preview["unavailable_sources"]
    assert repository.rows("marketing_signals")==[]


def test_safe_default_apply_idempotence_and_audit(tmp_path):
    repository,autopilot,_=foundation(tmp_path)
    with pytest.raises(PermissionError): autopilot.run()
    repository.set_automation(True,"admin")
    assert autopilot.run("admin")=={"mode":"APPLY","mutated":True,"signals":1,"opportunities":1,"proposals":1}
    assert autopilot.run("admin")=={"mode":"APPLY","mutated":True,"signals":0,"opportunities":0,"proposals":0}
    assert len(repository.rows("marketing_audit"))>=4


def test_scoring_uses_only_available_inputs_and_sales_is_explicitly_unavailable():
    result=ConfigurableScoringEngine({"media_quality_score":2}).score({"media_quality_score":80,"sales_score":None})
    assert result=={"score":80.0,"components":{"media_quality_score":80.0},"unavailable":["sales_score"]}
    assert not UnavailableSalesMarketingData().metrics("drc:p:1","P14D")["units_sold"].available


def test_manual_batch_workflow_approval_rejection_and_schedule(tmp_path):
    repository,autopilot,key=foundation(tmp_path)
    proposal=autopilot.create_manual_proposal("Gamme Hyper",[key],"Sélection humaine explicite","admin")
    autopilot.transition(proposal,"READY_FOR_REVIEW","admin")
    autopilot.transition(proposal,"APPROVED","admin")
    schedule=autopilot.schedule(proposal,"INSTAGRAM",(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"Europe/Paris","admin")
    assert schedule.startswith("schedule:") and repository.rows("marketing_proposals")[0]["status"]=="SCHEDULED"
    rejected=autopilot.create_manual_proposal("Refus",[key],"Test humain","admin")
    autopilot.transition(rejected,"READY_FOR_REVIEW","admin")
    with pytest.raises(ValueError): autopilot.transition(rejected,"REJECTED","admin")
    assert autopilot.transition(rejected,"REJECTED","admin","Hors stratégie")["status"]=="REJECTED"


def test_expiration_brand_kit_and_additive_reconnect(tmp_path):
    database=tmp_path/"marketing.db";sqlite3.connect(database).execute("CREATE TABLE legacy_kept(value TEXT)").connection.commit()
    repository,autopilot,_=foundation(tmp_path);repository.set_automation(True,"admin");autopilot.run()
    assert autopilot.expire((datetime.now(timezone.utc)+timedelta(days=15)).isoformat())==1
    assert BrandKit().logo_asset=="/drcloud-logo.png" and "packaging modifié" in BrandKit().forbidden_elements
    reopened=MarketingRepository(database)
    assert reopened.rows("marketing_opportunities")[0]["status"]=="EXPIRED"
    assert reopened.db.execute("SELECT name FROM sqlite_master WHERE name='legacy_kept'").fetchone()


def test_marketing_web_auth_csrf_and_cockpit(configured):
    app,_=configured
    assert request(app,"/marketing")[0]=="303 See Other"
    _,cookie=login(app);status,_,body=request(app,"/marketing",cookie=cookie)
    assert status=="200 OK" and "Marketing Autopilot" in body.decode()
    assert request(app,"/api/marketing/autopilot/preview","POST",{},cookie)[0]=="403 Forbidden"
    csrf=app._session({"HTTP_COOKIE":cookie})["csrf"]
    status,_,body=request(app,"/api/marketing/autopilot/preview","POST",{},cookie,{"X-CSRF-Token":csrf})
    assert status=="200 OK" and json.loads(body)["mutated"] is False


def test_creative_ai_frontend_has_complete_human_review_contract():
    html=Path("src/dr_cloud_sync/static/marketing.html").read_text()
    script=Path("src/dr_cloud_sync/static/marketing.js").read_text()
    for label in ("Générer","Régénérer","Approuver","Refuser","PREVIEW","PRESERVE_ORIGINAL"):
        assert label in html+script
    assert "publication" in html.lower()


def test_creative_api_requires_auth_and_csrf_and_runs_human_workflow(configured):
    app,_=configured
    product=app.os_repository.all()[0]
    app.media.repository.add(ProductMedia(
        "media:creative-api",product.drcloud_product_key,"IMAGE",MediaRole.PRIMARY,
        MediaSource.PRESTASHOP,"products/api.png","image/png",800,800,10,"b"*64,
        marketing_usage=MarketingUsage.ALLOWED,protected_original=True,
        usages=("catalogue","marketing")),[])
    proposal=app.marketing.create_manual_proposal(
        "Creative API",[product.drcloud_product_key],"Review API","admin")
    base=f"/api/marketing/proposals/{proposal}/creative"
    assert request(app,base)[0]=="303 See Other"
    _,cookie=login(app)
    assert request(app,base+"/generate","POST",{},cookie)[0]=="403 Forbidden"
    csrf=app._session({"HTTP_COOKIE":cookie})["csrf"]
    headers={"X-CSRF-Token":csrf}
    status,_,body=request(app,base+"/generate","POST",{},cookie,headers)
    generated=json.loads(body)
    assert status=="200 OK" and generated["status"]=="READY_FOR_REVIEW"
    assert {a["format"] for a in generated["creative_assets"]}=={"STORY","SQUARE"}
    status,_,body=request(app,base,cookie=cookie)
    assert status=="200 OK" and json.loads(body)["reviewable"] is True
    status,_,body=request(app,base+"/approve","POST",{},cookie,headers)
    assert status=="200 OK" and json.loads(body)["status"]=="APPROVED"
    assert app.marketing_repository.rows("marketing_schedules")==[]
