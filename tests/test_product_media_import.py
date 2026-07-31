from io import BytesIO
import json
from pathlib import Path

from PIL import Image

from dr_cloud_sync.domain import Product
from dr_cloud_sync.media import LocalMediaStorage, ProductMediaService, SQLiteProductMediaRepository
from dr_cloud_sync.media_import import ProductMediaImportService, combination_exclusivity
from dr_cloud_sync.backup_service import BackupUnavailable
from dr_cloud_sync.repositories import SQLiteOSRepository


def png(color="red"):
    stream=BytesIO(); Image.new("RGB",(40,30),color).save(stream,"PNG"); return stream.getvalue()


class FakePrestaShop:
    def __init__(self, products, combinations, *, payload=None, mime="image/png"):
        self.rows={"products":products,"combinations":combinations}; self.payload=payload or png(); self.mime=mime; self.downloads=[]
    def iter_resource(self, resource): return iter(self.rows[resource])
    def download_product_image(self, product_id, image_id, **kwargs):
        self.downloads.append((str(product_id),str(image_id))); return self.payload,self.mime


class FullFakePrestaShop(FakePrestaShop):
    def __init__(self, products, combinations, *, option_values=(), options=()):
        super().__init__(products, combinations)
        self.rows.update(product_option_values=list(option_values), product_options=list(options))


def setup(tmp_path: Path, client, products):
    tmp_path.mkdir(parents=True,exist_ok=True)
    db=tmp_path/"drcloud.db"; catalogue=SQLiteOSRepository(db,products)
    media=ProductMediaService(SQLiteProductMediaRepository(db),LocalMediaStorage(tmp_path/"media"/"products"),catalogue,catalogue)
    return ProductMediaImportService(db,client,media,catalogue),media


def assoc(*ids): return {"associations":{"images":[{"id":value} for value in ids]}}


def test_hyper_max_explicit_combination_associations_and_priority(tmp_path):
    products=[Product(f"drc:100:{cid}",f"100:{cid}",100,cid,cid,f"Hyper Max {cid}") for cid in range(710,715)]
    expected={710:792,711:796,712:791,713:798,714:790}
    client=FakePrestaShop([{"id":100,**assoc(999)}],[{"id":cid,**assoc(image)} for cid,image in expected.items()])
    importer,_=setup(tmp_path,client,products); preview=importer.preview()
    assert [(int(x["combination_id"]),int(x["candidate_image_id"])) for x in preview["items"]]==list(expected.items())
    assert all(x["provenance"]=="COMBINATION_IMAGE" for x in preview["items"])


def test_parent_fallback_ambiguity_and_manual_primary(tmp_path):
    products=[Product("drc:1:10","1:10",1,10,1,"A"),Product("drc:2:20","2:20",2,20,2,"B"),Product("drc:3:30","3:30",3,30,3,"C")]
    client=FakePrestaShop([{"id":1,**assoc(11)},{"id":2,**assoc(21,22)},{"id":3,**assoc(31)}],[{"id":10},{"id":20},{"id":30,**assoc(39)}])
    importer,media=setup(tmp_path,client,products); media.add("drc:3:30",png("blue"))
    preview=importer.preview(); by_key={x["product_key"]:x for x in preview["items"]}
    assert by_key["drc:1:10"]["provenance"]=="PARENT_FALLBACK"
    assert by_key["drc:2:20"]["classification"]=="AMBIGUOUS"
    assert by_key["drc:3:30"]["classification"]=="EXISTING_PRIMARY"


def test_apply_validates_mime_continues_errors_and_is_idempotent(tmp_path):
    products=[Product("drc:1:10","1:10",1,10,1,"A"),Product("drc:1:11","1:11",1,11,2,"A")]
    client=FakePrestaShop([{"id":1}],[{"id":10,"id_product":1,**assoc(10,11)},
                                               {"id":11,"id_product":1,**assoc(10)}])
    importer,media=setup(tmp_path,client,products)
    protected=media.add("drc:1:11",png("blue")); preview=importer.preview()
    first=importer.apply(preview); second=importer.apply(importer.preview())
    assert first["summary"]["imported"]==1 and second["summary"]["processed"]==0
    imported=media.primary("drc:1:10")
    assert imported.source.value=="PRESTASHOP" and imported.marketing_usage.value=="UNKNOWN"
    assert imported.protected_original and '"combination_id":"10"' in imported.source_reference
    assert len(media.repository.list("drc:1:10"))==1
    assert media.primary("drc:1:11").media_id==protected.media_id
    assert first["summary"]["protected_primary_unchanged"] is True

    bad_products=[Product("drc:2:20","2:20",2,20,2,"B"),Product("drc:2:21","2:21",2,21,3,"B")]
    bad=FakePrestaShop([{"id":2}],[{"id":20,"id_product":2,**assoc(20,21)},
                                    {"id":21,"id_product":2,**assoc(20)}],
                       payload=b"<html>login</html>",mime="text/html")
    bad_importer,bad_media=setup(tmp_path/"bad",bad,bad_products)
    bad_media.add("drc:2:21",png("blue"))
    result=bad_importer.apply(bad_importer.preview())
    assert result["summary"]["failed"]==1 and bad_media.primary("drc:2:20") is None


def test_backup_is_verified_before_download_and_failure_is_fail_closed(tmp_path):
    product=Product("drc:1:10","1:10",1,10,1,"A")
    client=FakePrestaShop([{"id":1}],[{"id":10,**assoc(11)}])
    importer,media=setup(tmp_path,client,[product])
    class BrokenBackup:
        def create(self, *args, **kwargs): raise BackupUnavailable("backup unavailable")
    importer.backup_service=BrokenBackup()
    try: importer.apply(importer.preview())
    except BackupUnavailable: pass
    else: raise AssertionError("apply must abort")
    assert client.downloads==[] and media.primary(product.drcloud_product_key) is None
    job=importer.jobs.list_recent(1)[0]
    assert job.status.value=="FAILED" and "backup unavailable" in (job.error_message or "")


def test_apply_repreviews_protects_new_primary_and_audits_result(tmp_path):
    product=Product("drc:1:10","1:10",1,10,1,"A")
    client=FakePrestaShop([{"id":1}],[{"id":10,**assoc(11)}])
    importer,media=setup(tmp_path,client,[product]); stale=importer.preview()
    manual=media.add(product.drcloud_product_key,png("blue"),actor="operator")
    result=importer.apply(stale,actor="operator")
    assert client.downloads==[] and media.primary(product.drcloud_product_key).media_id==manual.media_id
    assert result["summary"]["ignored_primary_existing"]==1
    events=[a for a in importer.catalogue.activities() if a.event_type=="PRESTASHOP_MEDIA_IMPORT"]
    assert len(events)==1 and events[0].metadata["actor"]=="operator"
    assert "PRESTASHOP_API_KEY" not in str(result)+str(events[0].metadata)


def test_ambiguous_diagnostic_is_actionable_and_never_downloaded(tmp_path):
    product=Product("drc:1:10","1:10",1,10,1,"Produit",variant_name="Rouge")
    client=FakePrestaShop([{"id":1}],[{"id":10,**assoc(11,12)}])
    importer,media=setup(tmp_path,client,[product]); item=importer.preview()["items"][0]
    assert (item["product"],item["variant"],item["product_id"],item["combination_id"])==("Produit","Rouge","1","10")
    assert item["candidates"]==["11","12"] and item["ambiguity_reason"]
    result=importer.apply(); assert result["summary"]["ambiguous"]==1
    assert client.downloads==[] and media.primary(product.drcloud_product_key) is None


def test_read_only_diagnostic_classifies_real_relationship_cases(tmp_path):
    products=[
        Product("drc:1:10","1:10",1,10,1,"A",variant_name="Rouge",attributes={"Couleur":"Rouge"}),
        Product("drc:2:20","2:20",2,20,2,"B"),
        Product("drc:3:30","3:30",3,30,3,"C"),
        Product("drc:4:40","4:40",4,40,4,"D"),
        Product("drc:5:50","5:50",5,50,5,"E"),
    ]
    combinations=[{"id":10,**assoc(11)},{"id":20,**assoc(21,22)},{"id":30},
                  {"id":40,**assoc(41)},{"id":50}]
    parents=[{"id":1,**assoc(19)},{"id":2,**assoc(29)},{"id":3,**assoc(31,32)},
             {"id":4,**assoc(49)},{"id":5}]
    importer,media=setup(tmp_path,FullFakePrestaShop(parents,combinations),products)
    protected=media.add("drc:4:40",png("blue"))
    before=media.repository.db.execute("SELECT * FROM product_media ORDER BY media_id").fetchall()
    report=importer.preview(); by_key={item["product_key"]:item for item in report["items"]}
    after=media.repository.db.execute("SELECT * FROM product_media ORDER BY media_id").fetchall()

    assert by_key["drc:1:10"]["projected_classification"]=="SAFE_RESOLVABLE"
    assert by_key["drc:1:10"]["candidate_image_id"]=="11"  # combination beats parent
    assert by_key["drc:2:20"]["ambiguity_cause"]=="MULTIPLE_COMBINATION_IMAGES"
    assert by_key["drc:3:30"]["ambiguity_cause"]=="MULTIPLE_PARENT_IMAGES"
    assert by_key["drc:4:40"]["classification"]=="EXISTING_PRIMARY"
    assert by_key["drc:4:40"]["existing_primary"]["media_id"]==protected.media_id
    assert by_key["drc:5:50"]["projected_classification"]=="NO_DATA"
    assert report["summary"]["ambiguity_causes"]=={
        "MULTIPLE_COMBINATION_IMAGES":1,"MULTIPLE_PARENT_IMAGES":1}
    assert report["primary_integrity"]["duplicate_primary_count"]==0
    assert before==after and importer.client.downloads==[]


def decision(candidates, siblings, **kwargs):
    return combination_exclusivity(candidates,siblings,
        explicitly_associated_image_ids=kwargs.pop("explicit",candidates),**kwargs)


def test_combination_exclusivity_set_rules():
    assert decision([1,2,3],[[1,2,3]])["classification"]=="AMBIGUOUS_REMAINING"
    safe=decision([1,2,3,4],[[1,2,3]])
    assert safe["classification"]=="SAFE_BY_COMBINATION_EXCLUSIVITY"
    assert safe["candidate_image_id"]=="4" and safe["exclusive_image_ids"]==["4"]
    assert decision([1,4,5],[[1]])["classification"]=="AMBIGUOUS_REMAINING"
    assert decision([1,2],[[1]],explicit=[1])["classification"]=="AMBIGUOUS_REMAINING"
    assert decision([1,2],[[1]],existing_primary_image_id="9")["classification"]=="AMBIGUOUS_REMAINING"


def test_each_variant_may_have_one_exclusive_and_preview_never_mutates(tmp_path):
    products=[Product(f"drc:56:{cid}",f"56:{cid}",56,cid,cid,"DUM VICTORIA",variant_name=name)
              for cid,name in [(165,"BLANC"),(166,"NOIR"),(167,"SILVER"),(168,"TRANSPARENT"),(169,"GOLD")]]
    combinations=[{"id":165,"id_product":56,**assoc(204,205,206,207)},
        {"id":166,"id_product":56,**assoc(205,206,207,208)},
        {"id":167,"id_product":56,**assoc(203,205,206,207)},
        {"id":168,"id_product":56,**assoc(202,205,206,207)},
        {"id":169,"id_product":56,**assoc(198,199,200,201)}]
    importer,media=setup(tmp_path,FakePrestaShop([{"id":56,**assoc(*range(198,209))}],combinations),products)
    before=media.repository.db.execute("SELECT * FROM product_media ORDER BY media_id").fetchall()
    report=importer.preview(); by_id={int(x["combination_id"]):x for x in report["items"]}
    after=media.repository.db.execute("SELECT * FROM product_media ORDER BY media_id").fetchall()
    assert {cid:by_id[cid]["candidate_image_id"] for cid in (165,166,167,168)}=={165:"204",166:"208",167:"203",168:"202"}
    assert all(by_id[cid]["projected_classification"]=="SAFE_BY_COMBINATION_EXCLUSIVITY" for cid in (165,166,167,168))
    assert by_id[169]["projected_classification"]=="AMBIGUOUS_REMAINING"
    assert by_id[169]["exclusive_image_ids"]==["198","199","200","201"]
    assert report["summary"]["safe_by_combination_exclusivity"]==4
    assert report["summary"]["ambiguous_remaining"]==1
    assert before==after and importer.client.downloads==[]
    assert report["families"][0]["common_image_ids"]==[] and len(report["families"][0]["matrix"])==5


def test_exclusivity_preview_is_the_only_apply_candidate_and_is_idempotent(tmp_path):
    products=[Product("drc:1:10","1:10",1,10,1,"A"),Product("drc:1:11","1:11",1,11,2,"A")]
    combinations=[{"id":10,"id_product":1,**assoc(1,2)},{"id":11,"id_product":1,**assoc(1,3)}]
    importer,media=setup(tmp_path,FakePrestaShop([{"id":1,**assoc(1,2,3)}],combinations),products)
    preview=importer.preview()
    assert preview["summary"]["safe_by_combination_exclusivity"]==2
    result=importer.apply(preview)
    assert result["summary"]["processed"]==2 and result["summary"]["imported"]==2
    assert importer.client.downloads==[("1","2"),("1","3")]
    assert all(media.primary(product.drcloud_product_key) is not None for product in products)
    again=importer.apply(importer.preview())
    assert again["summary"]["processed"]==again["summary"]["imported"]==0


def test_manual_resolution_validates_candidates_identity_backup_and_persists(tmp_path):
    products=[Product("drc:1:10","1:10",1,10,1,"Variant",variant_name="Rouge"),
              Product("drc:2:0","2:0",2,None,2,"Simple")]
    client=FakePrestaShop([{"id":1,**assoc(11,12)},{"id":2,**assoc(21,22)}],
                         [{"id":10,"id_product":1,**assoc(11,12)}])
    importer,media=setup(tmp_path,client,products)
    before=importer._business_fingerprint()
    try: importer.resolve_manual("drc:1:10","999",actor="admin")
    except ValueError as exc: assert "candidates" in str(exc)
    else: raise AssertionError("non-candidate accepted")
    assert client.downloads==[]

    resolved=importer.resolve_manual("drc:1:10","12",actor="admin")
    primary=media.primary("drc:1:10"); reference=json.loads(primary.source_reference)
    assert resolved["status"]=="RESOLVED" and reference=={
        "resource":"images/products/1/12","image_id":"12","product_id":"1",
        "combination_id":"10","provenance":"MANUAL_AMBIGUITY_RESOLUTION"}
    assert importer._business_fingerprint()==before
    assert resolved["summary"]["existing_primary"]==1
    assert resolved["summary"]["ambiguous_remaining"]==1
    assert importer.resolve_manual("drc:1:10","12")["status"]=="ALREADY_RESOLVED"
    assert client.downloads==[("1","12")]
    try: importer.resolve_manual("drc:1:10","11")
    except ValueError as exc: assert "PRIMARY" in str(exc)
    else: raise AssertionError("primary overwritten")
    assert media.primary("drc:1:10").media_id==primary.media_id

    # Parent candidates and a null combination identity are valid for a simple product.
    importer.resolve_manual("drc:2:0","21",actor="admin")
    simple=json.loads(media.primary("drc:2:0").source_reference)
    assert simple["product_id"]=="2" and simple["combination_id"] is None
    reopened=SQLiteProductMediaRepository(tmp_path/"drcloud.db")
    assert reopened.primary("drc:1:10").media_id==primary.media_id
    events=[x for x in importer.catalogue.activities() if x.event_type=="PRESTASHOP_MEDIA_MANUALLY_RESOLVED"]
    assert [(x.drcloud_product_key,x.metadata["image_id"]) for x in events]==[("drc:1:10","12"),("drc:2:0","21")]


def test_manual_resolution_simulates_final_478_counters_without_catalogue_writes(tmp_path):
    products=[]; parents=[]; combinations=[]
    for index in range(478):
        pid=index+1; key=f"drc:{pid}:0"
        products.append(Product(key,f"{pid}:0",pid,None,pid,f"Produit {pid}"))
        parents.append({"id":pid,**assoc(pid*10+1,pid*10+2)})
    client=FakePrestaShop(parents,combinations)
    importer,media=setup(tmp_path,client,products)
    # Model the 467 protected production PRIMARYs; only the eleven ambiguous cases use the resolver.
    for product in products[:467]: media.add(product.drcloud_product_key,png("blue"))
    catalogue_before=importer._business_fingerprint()
    for product in products[467:]:
        importer.resolve_manual(product.drcloud_product_key,str(product.product_id*10+1),actor="test")
    final=importer.preview()["summary"]
    assert final["processed"]==478 and final["existing_primary"]==478
    assert final["no_image"]==final["ambiguous"]==final["ambiguous_remaining"]==0
    assert final["safe_by_combination_exclusivity"]==0
    assert importer._business_fingerprint()==catalogue_before
    assert len(client.downloads)==11  # GET downloads only; the fake exposes no write method.
