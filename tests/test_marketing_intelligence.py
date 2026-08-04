from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dr_cloud_sync.marketing import MarketingRepository
from dr_cloud_sync.marketing_intelligence import MarketingIntelligenceService, SocialAnalyticsLiveService


class Catalogue:
    def all(self): return [SimpleNamespace(drcloud_product_key="product:1",name="Produit test")]
class Stock:
    quantity=100
    def position(self,key): return self
class Sales:
    def metrics(self,key,days): return {"units_sold":0}


def test_social_live_not_configured_and_missing_values_are_never_zero(tmp_path):
    repo=MarketingRepository(tmp_path/"db.sqlite")
    result=SocialAnalyticsLiveService(repo.db).cockpit()
    assert result["status"]=="NOT_CONFIGURED" and result["connected_accounts"]==0
    metric=result["platforms"][0]["metrics"]["reach"]
    assert metric["value"] is None and metric["availability"]=="NOT_CONFIGURED"
    assert result["charts"]["content_performance"]==[] and result["top_contents"]["views"]==[]


def test_social_live_partial_stale_normalization_and_native_metadata(tmp_path):
    repo=MarketingRepository(tmp_path/"db.sqlite"); old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    service=SocialAnalyticsLiveService(repo.db,stale_hours=1)
    with repo.db:
        repo.db.execute("INSERT INTO social_connections(connection_id,channel,account_id,status,credential_reference,display_name) VALUES('c','INSTAGRAM','a','CONNECTED','opaque','Compte')")
        repo.db.execute("INSERT INTO social_analytics_snapshots VALUES('post','INSTAGRAM',?,12,NULL,NULL,NULL,NULL,NULL,?)",(old,'{"native_metric": 3}'))
    result=service.cockpit()
    instagram=result["platforms"][0]
    assert instagram["status"]=="STALE" and instagram["metrics"]["reach"]["value"]==12
    assert instagram["metrics"]["views"]["value"] is None


def test_stock_margin_proposals_are_reviewed_idempotent_and_non_tariff(tmp_path):
    repo=MarketingRepository(tmp_path/"db.sqlite")
    service=MarketingIntelligenceService(repo,Catalogue(),Stock(),Sales())
    first=service.generate(); second=service.generate()
    assert first["generated"]==2 and second["generated"]==0
    proposals=repo.rows("marketing_proposals")
    assert all(p["status"]=="READY_FOR_REVIEW" for p in proposals)
    assert all(p["creative_brief"]["discount"] is None for p in proposals)
    assert all(p["creative_brief"]["evidence"]["availability"]=="PARTIAL" for p in proposals)


def test_learning_loop_correlation_baseline_uplift_low_confidence_and_replay(tmp_path):
    repo=MarketingRepository(tmp_path/"db.sqlite"); service=MarketingIntelligenceService(repo,Catalogue())
    # A proposal is sufficient for the measured foreign-domain link.
    with repo.db:
        repo.db.execute("INSERT INTO marketing_opportunities VALUES('o','TEST','[\"product:1\"]','[]',1,'{}','test',?,NULL,'PROPOSED','o-key')",(datetime.now(timezone.utc).isoformat(),))
        repo.db.execute("INSERT INTO marketing_proposals VALUES('p','o','T','O','R',1,'APPROVED','[]','[]','H','B','C','[]','','{}',?,?,'p-key')",(datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat()))
    args=dict(product_id="product:1",channel="TEST_INTERNAL",format="SQUARE",published_at="2026-08-01T00:00:00+00:00",social_metrics={"reach":100,"clicks":None},sales_before=2,sales_after=4)
    first=service.measure("p","content:1",**args); second=service.measure("p","content:1",**args)
    assert first["measurements"][0]["attribution"]=="CORRELATED"
    assert first["measurements"][0]["uplift"]["sales"]==1 and first["measurements"][0]["confidence"]<.6
    assert len(second["measurements"])==1 and repo.rows("marketing_proposals")[0]["status"]=="MEASURED"


def test_tracked_evidence_is_likely_not_causal(tmp_path):
    repo=MarketingRepository(tmp_path/"db.sqlite"); service=MarketingIntelligenceService(repo,Catalogue())
    result=service.measure("missing","content",product_id="product:1",channel="TEST_INTERNAL",format="VIDEO",published_at="2026-08-01T00:00:00+00:00",social_metrics={"clicks":2},sales_before=1,sales_after=2,tracked_link=True)
    assert result["measurements"][0]["attribution"]=="LIKELY"
    assert "CAUSED" not in str(result)
