from pathlib import Path

from dr_cloud_sync.marketing import MarketingRepository
from dr_cloud_sync.marketing_intelligence import MarketingIntelligenceService, SocialAnalyticsLiveService
from dr_cloud_sync.marketing_operations import MarketingOperationsService


class Catalogue:
    def all(self): return []


def service(tmp_path):
    repo=MarketingRepository(tmp_path/"operations.db")
    intelligence=MarketingIntelligenceService(repo,Catalogue())
    return repo,MarketingOperationsService(repo,SocialAnalyticsLiveService(repo.db),intelligence)


def test_empty_cockpit_is_truthful_and_provider_neutral(tmp_path):
    _, operations=service(tmp_path); result=operations.cockpit()
    assert result["provider_status"] == "NOT_CONFIGURED"
    assert result["kpis"][3]["value"] is None and result["kpis"][6]["value"] is None
    assert result["notifications"][0]["type"] == "PROVIDER_ABSENT"


def test_search_is_paginated_and_recommendation_is_explained(tmp_path):
    repo,operations=service(tmp_path); stamp="2026-08-04T12:00:00+00:00"
    with repo.db:
        repo.db.execute("INSERT INTO marketing_opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?)",("opp:1","OVERSTOCK",'[\"product:1\"]','[]',82,'{\"score\":82,\"components\":{\"stock\":95,\"margin\":68,\"social\":null}}',"Surstock important",stamp,None,"OPEN","opp-key"))
        repo.db.execute("INSERT INTO marketing_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("proposal:1","opp:1","Produit dormant","Rotation","Surstock",80,"READY_FOR_REVIEW",'[\"INSTAGRAM\"]','[\"STORY\"]',"Titre","Texte","Voir","[]","","{}",stamp,stamp,"proposal-key"))
    explanation=operations.cockpit()["recommendations"][0]["explanation"]
    assert explanation["score"]["total"] == 82 and "social" in explanation["missing_data"]
    result=operations.review({"q":"dormant","page_size":"1"})
    assert result["total"] == 1 and result["external_publication_enabled"] is False


def test_operations_pages_keep_internal_preview_and_no_publish_action():
    html=Path("src/dr_cloud_sync/static/marketing-operations.html").read_text()
    js=Path("src/dr_cloud_sync/static/marketing-operations.js").read_text()
    assert "Prévisualisation interne" in js and "publication externe" in html
    assert ">Publier<" not in html+js and "setTimeout(load,300)" in js
