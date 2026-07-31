from dr_cloud_sync.creative_ai import CreativeAIService, CreativeGenerationError
from dr_cloud_sync.domain import MarketingUsage, MediaRole, MediaSource, Product, ProductMedia, ProductStatus
from dr_cloud_sync.marketing import MarketingAutopilot, MarketingRepository
from dr_cloud_sync.media import SQLiteProductMediaRepository
from dr_cloud_sync.repositories import MemoryCatalogRepository


def foundation(tmp_path):
    database=tmp_path/"creative.db"
    key="drc:p:1"
    product=Product(key,"p:1",1,0,"sc-1","Hyper Max 50K — PEACH ICE","",status=ProductStatus.ACTIVE)
    catalogue=MemoryCatalogRepository([product])
    media=SQLiteProductMediaRepository(database)
    media.add(ProductMedia("media:1",key,"IMAGE",MediaRole.PRIMARY,MediaSource.PRESTASHOP,
        "products/one.png","image/png",800,800,10,"a"*64,marketing_usage=MarketingUsage.ALLOWED,
        protected_original=True,usages=("catalogue","marketing")),[])
    repository=MarketingRepository(database)
    autopilot=MarketingAutopilot(repository,catalogue,media)
    proposal=autopilot.create_manual_proposal("Hyper Max",[key],"Mise en avant produit","admin")
    return repository,catalogue,media,proposal,key


def test_generation_uses_canonical_product_and_primary_media(tmp_path):
    repository,catalogue,media,proposal,key=foundation(tmp_path)
    result=CreativeAIService(repository,catalogue,media).generate(proposal,"admin")

    assert result["status"]=="READY_FOR_REVIEW"
    assert result["headline"]=="Hyper Max 50K — PEACH ICE"
    assert "Hyper Max 50K — PEACH ICE" in result["body"]
    assert result["cta"]=="Disponible chez DrCloud."
    assert result["idempotent"] is False
    assert len(result["creative_assets"])==2
    assert {asset["format"] for asset in result["creative_assets"]}=={"STORY","SQUARE"}
    for asset in result["creative_assets"]:
        assert asset["source"]=="CREATIVE_AI" and asset["status"]=="PREVIEW"
        assert asset["packaging_policy"]=="PRESERVE_ORIGINAL"
        assert '"media_id": "media:1"' in asset["media_reference"]


def test_generation_is_idempotent_for_same_factual_snapshot(tmp_path):
    repository,catalogue,media,proposal,_=foundation(tmp_path)
    service=CreativeAIService(repository,catalogue,media)
    first=service.generate(proposal,"admin")
    second=service.generate(proposal,"admin")

    assert first["idempotent"] is False and second["idempotent"] is True
    assert len(second["creative_assets"])==2
    events=[row for row in repository.rows("marketing_audit",limit=100) if row["event_type"]=="CREATIVE_GENERATED"]
    assert len(events)==1


def test_generation_fails_closed_without_primary_media(tmp_path):
    repository,catalogue,media,proposal,key=foundation(tmp_path)
    with media.db:
        media.db.execute("DELETE FROM product_media WHERE product_key=?",(key,))

    try:
        CreativeAIService(repository,catalogue,media).generate(proposal,"admin")
    except CreativeGenerationError as exc:
        assert "PRIMARY media missing" in str(exc)
    else:
        raise AssertionError("generation accepted a product without PRIMARY media")


def test_generation_preserves_canonical_product_snapshot(tmp_path):
    repository,catalogue,media,proposal,_=foundation(tmp_path)
    before=[(p.drcloud_product_key,p.name,p.ean,p.reference) for p in catalogue.all()]

    CreativeAIService(repository,catalogue,media).generate(proposal,"admin")

    after=[(p.drcloud_product_key,p.name,p.ean,p.reference) for p in catalogue.all()]
    assert after==before


def test_regeneration_is_explicit_idempotent_and_audited(tmp_path):
    repository,catalogue,media,proposal,_=foundation(tmp_path)
    service=CreativeAIService(repository,catalogue,media)
    first=service.generate(proposal,"admin")
    regenerated=service.regenerate(proposal,"reviewer")

    assert regenerated["idempotent"] is True
    assert {a["creative_id"] for a in regenerated["creative_assets"]} == {
        a["creative_id"] for a in first["creative_assets"]}
    audit=next(a for a in repository.rows("marketing_audit")
               if a["event_type"]=="CREATIVE_REGENERATED")
    assert audit["actor"]=="reviewer"
    assert set(audit["details"]["formats"])=={"STORY","SQUARE"}
    assert audit["details"]["packaging_policy"]=="PRESERVE_ORIGINAL"


def test_creative_detail_exposes_canonical_facts_brand_and_brief(tmp_path):
    repository,catalogue,media,proposal,key=foundation(tmp_path)
    detail=CreativeAIService(repository,catalogue,media).generate(proposal,"admin")

    assert detail["products"][0]["product_key"]==key
    assert detail["products"][0]["ean"]==""
    assert detail["products"][0]["primary_media_url"].startswith("/media/media:1/original")
    assert detail["creative_brief"]["packaging_policy"]=="PRESERVE_ORIGINAL"
    assert detail["brand_kit"]["colors"]==("#111111","#FFFFFF","#16A366")
