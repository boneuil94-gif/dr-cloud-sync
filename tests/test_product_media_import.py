from io import BytesIO
from pathlib import Path

from PIL import Image

from dr_cloud_sync.domain import Product
from dr_cloud_sync.media import LocalMediaStorage, ProductMediaService, SQLiteProductMediaRepository
from dr_cloud_sync.media_import import ProductMediaImportService
from dr_cloud_sync.repositories import SQLiteOSRepository


def png(color="red"):
    stream=BytesIO(); Image.new("RGB",(40,30),color).save(stream,"PNG"); return stream.getvalue()


class FakePrestaShop:
    def __init__(self, products, combinations, *, payload=None, mime="image/png"):
        self.rows={"products":products,"combinations":combinations}; self.payload=payload or png(); self.mime=mime; self.downloads=[]
    def iter_resource(self, resource): return iter(self.rows[resource])
    def download_product_image(self, product_id, image_id, **kwargs):
        self.downloads.append((str(product_id),str(image_id))); return self.payload,self.mime


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
