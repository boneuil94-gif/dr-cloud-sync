from io import BytesIO
import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from dr_cloud_sync.domain import Product, ProductStatus
from dr_cloud_sync.media import (LocalMediaStorage, MediaError, ProductMediaService,
                                 SQLiteProductMediaRepository)
from dr_cloud_sync.os_admin import backup, restore_backup
from dr_cloud_sync.repositories import SQLiteOSRepository


def image_bytes(fmt="PNG", size=(48, 32), color="red"):
    stream=BytesIO(); Image.new("RGB",size,color).save(stream,fmt); return stream.getvalue()


@pytest.fixture
def media_service(tmp_path):
    database=tmp_path/"drcloud.db"
    product=Product("drc:12:0","12:0",12,None,24,"Produit","12345678",reference="REF")
    catalogue=SQLiteOSRepository(database,[product])
    return ProductMediaService(SQLiteProductMediaRepository(database),LocalMediaStorage(tmp_path/"media"/"products"),catalogue,catalogue), catalogue


def test_identity_primary_provenance_checksum_variants_and_reconnect(media_service, tmp_path):
    service,catalogue=media_service
    first=service.add("drc:12:0",image_bytes(),filename="../../photo.png",marketing_usage="ALLOWED",protected_original=True)
    second=service.add("drc:12:0",image_bytes(color="blue"),filename="other.png",source="MOBILE_CAMERA")
    assert first.media_id.startswith("media:") and "photo.png" not in first.media_id
    assert first.sha256==hashlib.sha256(service.storage.read(first.storage_reference)).hexdigest()
    assert first.marketing_usage.value=="ALLOWED" and first.protected_original
    assert service.repository.primary("drc:12:0").media_id==second.media_id
    assert service.repository.get(first.media_id).role.value=="SECONDARY"
    assert service.repository.variant(first.media_id,"THUMBNAIL")
    service.make_primary("drc:12:0",first.media_id)
    catalogue.set_status("drc:12:0",ProductStatus.ARCHIVED)
    service.disable("drc:12:0",first.media_id)
    assert not service.repository.get(first.media_id).active
    assert service.repository.get(second.media_id).source.value=="MOBILE_CAMERA"
    reopened=SQLiteProductMediaRepository(tmp_path/"drcloud.db")
    assert len(reopened.list("drc:12:0"))==2


def test_same_checksum_keeps_distinct_business_relations(tmp_path):
    db=tmp_path/"drcloud.db"; products=[Product(f"drc:{n}:0",f"{n}:0",n,None,n+10,f"P{n}") for n in (1,2)]
    catalogue=SQLiteOSRepository(db,products); service=ProductMediaService(SQLiteProductMediaRepository(db),LocalMediaStorage(tmp_path/"media"),catalogue,catalogue)
    one=service.add("drc:1:0",image_bytes()); two=service.add("drc:2:0",image_bytes())
    assert one.sha256==two.sha256 and one.media_id!=two.media_id and one.storage_reference!=two.storage_reference


@pytest.mark.parametrize("payload,name",[(b"not image","fake.jpg"),(b"<svg/>","x.svg")])
def test_content_validation_rejects_spoofing(media_service,payload,name):
    with pytest.raises(MediaError): media_service[0].add("drc:12:0",payload,filename=name)


def test_storage_rejects_traversal_and_absolute_paths(media_service):
    storage=media_service[0].storage
    for value in ("../drcloud.db","/etc/passwd","x/../../secret"):
        with pytest.raises(MediaError): storage.read(value)


def test_backup_restore_media_manifest_and_corruption(media_service,tmp_path):
    service,_=media_service; media=service.add("drc:12:0",image_bytes())
    (tmp_path/"catalogue.json").write_text('[{"name":"test"}]')
    (tmp_path/"catalogue-report.json").write_text('{"ready_for_inventory":true}')
    target=backup(tmp_path/"drcloud.db",tmp_path/"backups",environment="test",safe_mode=True)
    metadata=json.loads((target/"metadata.json").read_text())
    assert metadata["media"]["included"] and metadata["media"]["files"]
    restored=tmp_path/"restored"; restore_backup(target,restored)
    assert (restored/"drcloud.db").is_file() and (restored/"media"/"products"/media.storage_reference).is_file()
    victim=target/metadata["media"]["files"][0]["path"]; victim.write_bytes(b"corrupt")
    with pytest.raises(ValueError,match="corrompu"): restore_backup(target,tmp_path/"bad")
