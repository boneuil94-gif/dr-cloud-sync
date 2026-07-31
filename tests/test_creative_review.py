from dr_cloud_sync.creative_ai import CreativeAIService
from dr_cloud_sync.creative_review import CreativeReviewError, CreativeReviewService
from dr_cloud_sync.domain import MarketingUsage, MediaRole, MediaSource, Product, ProductMedia, ProductStatus
from dr_cloud_sync.marketing import MarketingAutopilot, MarketingRepository
from dr_cloud_sync.media import SQLiteProductMediaRepository
from dr_cloud_sync.repositories import MemoryCatalogRepository


def generated(tmp_path):
    database=tmp_path/"review.db"; key="drc:p:1"
    product=Product(key,"p:1",1,0,"sc-1","Hyper Max 50K — PEACH ICE","",status=ProductStatus.ACTIVE)
    catalogue=MemoryCatalogRepository([product]); media=SQLiteProductMediaRepository(database)
    media.add(ProductMedia("media:1",key,"IMAGE",MediaRole.PRIMARY,MediaSource.PRESTASHOP,
        "products/one.png","image/png",800,800,10,"a"*64,marketing_usage=MarketingUsage.ALLOWED,
        protected_original=True,usages=("catalogue","marketing")),[])
    repository=MarketingRepository(database)
    proposal=MarketingAutopilot(repository,catalogue,media).create_manual_proposal(
        "Hyper Max",[key],"Mise en avant produit","admin")
    CreativeAIService(repository,catalogue,media).generate(proposal,"admin")
    return repository,proposal


def test_generated_proposal_is_reviewable(tmp_path):
    repository,proposal=generated(tmp_path)
    detail=CreativeReviewService(repository).detail(proposal)
    assert detail["reviewable"] is True
    assert detail["status"]=="READY_FOR_REVIEW"
    assert len(detail["creative_assets"])==2


def test_approve_promotes_proposal_and_assets_without_scheduling(tmp_path):
    repository,proposal=generated(tmp_path)
    before_schedules=len(repository.rows("marketing_schedules",limit=100))
    result=CreativeReviewService(repository).approve(proposal,"admin")
    assert result["status"]=="APPROVED"
    assert {a["status"] for a in result["creative_assets"]}=={"APPROVED"}
    assert len(repository.rows("marketing_schedules",limit=100))==before_schedules
    events=[x for x in repository.rows("marketing_audit",limit=100) if x["event_type"]=="CREATIVE_APPROVED"]
    assert len(events)==1


def test_reject_requires_reason_and_marks_preview_assets(tmp_path):
    repository,proposal=generated(tmp_path); review=CreativeReviewService(repository)
    try: review.reject(proposal,"  ","admin")
    except CreativeReviewError: pass
    else: raise AssertionError("empty rejection reason accepted")
    result=review.reject(proposal,"Visuel à retravailler","admin")
    assert result["status"]=="REJECTED"
    assert {a["status"] for a in result["creative_assets"]}=={"REJECTED"}


def test_approval_fails_closed_if_preview_packaging_policy_is_tampered(tmp_path):
    repository,proposal=generated(tmp_path)
    with repository.db:
        repository.db.execute("UPDATE marketing_assets SET packaging_policy='ALTER' WHERE proposal_id=?",(proposal,))
    try: CreativeReviewService(repository).approve(proposal,"admin")
    except CreativeReviewError: pass
    else: raise AssertionError("unsafe creative approved")
    assert CreativeReviewService(repository).detail(proposal)["status"]=="READY_FOR_REVIEW"
