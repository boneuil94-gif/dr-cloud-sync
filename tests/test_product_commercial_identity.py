import json

from dr_cloud_sync.domain import Product
from dr_cloud_sync.repositories import SQLiteOSRepository


def product(key, variant="", attributes=None):
    return Product(f"drc:prestashop:100:{key}", f"prestashop:100:{key}", 100, key,
                   f"sc-{key}", "Hyper Max Prime 50K", base_name="Hyper Max Prime 50K",
                   variant_name=variant, attributes=attributes or {}, variant_source="PRESTASHOP")


def test_variants_have_distinct_central_display_names_and_stable_identity():
    variants=[product(710,"Blueberry"),product(711,"Watermelon"),product(712,"Mint")]
    identities=[p.drcloud_product_key for p in variants]
    assert [p.display_name for p in variants] == [
        "Hyper Max Prime 50K — Blueberry", "Hyper Max Prime 50K — Watermelon",
        "Hyper Max Prime 50K — Mint"]
    variants[0].base_name="Renamed"; variants[1].variant_name="Melon"; variants[2].ean="123"
    assert [p.drcloud_product_key for p in variants] == identities


def test_open_structured_attributes_support_none_one_many_and_unknown():
    assert product(1).attributes == {}
    assert product(2,attributes={"Saveur":"Blueberry"}).attributes == {"Saveur":"Blueberry"}
    assert product(3,attributes={"Saveur":"Mint","Nicotine":"2%"}).attributes["Nicotine"] == "2%"
    assert product(4,attributes={"Attribut futur":"Valeur"}).attributes["Attribut futur"] == "Valeur"


def test_additive_migration_backfills_metadata_without_replacing_key_or_canonical_ean(tmp_path):
    path=tmp_path/"catalogue.sqlite"
    initial=product(710); initial.ean="4006381333931"
    repo=SQLiteOSRepository(path,[initial]); repo.db.close()
    enriched=product(710,"Peach Ice",{"AL FAKHER 50K":"PEACH ICE"}); enriched.ean="external"
    reopened=SQLiteOSRepository(path,[enriched]); stored=reopened.get(initial.drcloud_product_key)
    assert stored.drcloud_product_key == initial.drcloud_product_key
    assert stored.variant_name == "Peach Ice" and stored.attributes == {"AL FAKHER 50K":"PEACH ICE"}
    assert stored.ean == "4006381333931"
    columns={r[1] for r in reopened.db.execute("PRAGMA table_info(drcloud_products)")}
    assert {"base_name","variant_name","attributes_json","ean_source"} <= columns


