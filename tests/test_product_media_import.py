from io import BytesIO
from pathlib import Path

from PIL import Image

from dr_cloud_sync.domain import Product
from dr_cloud_sync.media import LocalMediaStorage, ProductMediaService, SQLiteProductMediaRepository
from dr_cloud_sync.media_import import ProductMediaImportService
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
    products=[Product("drc:1:10","1:10",1,10,1,"A")]
    client=FakePrestaShop([{"id":1}],[{"id":10,**assoc(11)}])
    importer,media=setup(tmp_path,client,products); preview=importer.preview()
    first=importer.apply(preview); second=importer.apply(importer.preview())
    assert first["summary"]["imported"]==1 and second["summary"]["processed"]==0
    imported=media.primary("drc:1:10")
    assert imported.source.value=="PRESTASHOP" and imported.marketing_usage.value=="UNKNOWN"
    assert imported.protected_original and '"combination_id":"10"' in imported.source_reference
    assert len(media.repository.list("drc:1:10"))==1

    bad_products=[Product("drc:2:20","2:20",2,20,2,"B")]
    bad=FakePrestaShop([{"id":2}],[{"id":20,**assoc(21)}],payload=b"<html>login</html>",mime="text/html")
    bad_importer,bad_media=setup(tmp_path/"bad",bad,bad_products)
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
