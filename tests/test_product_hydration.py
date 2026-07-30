from dr_cloud_sync.domain import Product
from dr_cloud_sync.hydration import (ProductHydrationService, ProductObservation,
                                     prestashop_observations, run_enrichment_job,
                                     valid_ean, variant_name)
from dr_cloud_sync.repositories import SQLiteOSRepository


def products():
    return [Product(f"drc:prestashop:100:{cid}", f"prestashop:100:{cid}", 100, cid,
                    f"sc-{cid}", "Hyper Max Prime 50K", base_name="Hyper Max Prime 50K")
            for cid in (710, 711, 712)]


def test_hydrates_three_existing_combinations_and_structured_attributes(tmp_path):
    repo=SQLiteOSRepository(tmp_path/"db.sqlite",products()); service=ProductHydrationService(repo)
    rows=[ProductObservation(p.drcloud_product_key,"PRESTASHOP",str(p.combination_id),
          variant_name=name,attributes={"Saveur":name}) for p,name in zip(products(),("Peach Ice","Love","Mint"))]
    result=service.hydrate(rows)
    assert result["enriched"] == 3
    assert [p.variant_name for p in repo.all()] == ["Peach Ice","Love","Mint"]
    assert repo.get(rows[0].product_key).attributes == {"Saveur":"Peach Ice"}


def test_manual_override_survives_later_observation_and_observed_value_remains(tmp_path):
    repo=SQLiteOSRepository(tmp_path/"db.sqlite",products()[:1]); service=ProductHydrationService(repo); key=products()[0].drcloud_product_key
    service.hydrate([ProductObservation(key,"PRESTASHOP","710",variant_name="Blueberry Mint")])
    service.update_manual(key,{"variant_name":"Blueberry Menthe"},"admin")
    result=service.hydrate([ProductObservation(key,"PRESTASHOP","710",variant_name="Blueberry Mint")])
    assert repo.get(key).variant_name == "Blueberry Menthe"
    assert repo.observations(key)[0]["variant_name"] == "Blueberry Mint"
    assert result["conflicts"] == 1


def test_ean_validation_conflict_and_manual_protection(tmp_path):
    assert valid_ean("4006381333931") and not valid_ean("4006381333932") and not valid_ean("")
    repo=SQLiteOSRepository(tmp_path/"db.sqlite",products()[:2]); service=ProductHydrationService(repo); first,second=products()[:2]
    service.update_manual(first.drcloud_product_key,{"ean":"4006381333931"},"admin")
    result=service.hydrate([ProductObservation(second.drcloud_product_key,"PRESTASHOP","711",ean="4006381333931")])
    assert not repo.get(second.drcloud_product_key).ean and result["conflicts"] == 1
    service.hydrate([ProductObservation(first.drcloud_product_key,"PRESTASHOP","710",ean="9780201379624")])
    assert repo.get(first.drcloud_product_key).ean == "4006381333931"


def test_variant_renderer_is_generic_and_deterministic():
    assert variant_name({"Saveur":"Blueberry Mint"}) == "Blueberry Mint"
    assert variant_name({"Saveur":"Blueberry Mint","Nicotine":"20 mg"}) == "Blueberry Mint · 20 mg"


class FakeClient:
    rows={
      "products":[{"id":100,"name":"Hyper Max","reference":"PARENT"}],
      "combinations":[{"id":710,"id_product":100,"reference":"COMBO","ean13":"4006381333931","associations":{"product_option_values":[{"id":455}]}}],
      "product_options":[{"id":40,"name":"Saveur"}],
      "product_option_values":[{"id":455,"id_attribute_group":40,"name":"Peach Ice"}],
    }
    def iter_resource(self,name): return iter(self.rows[name])


def test_prestashop_resolves_real_option_group_and_combination_fields():
    row=prestashop_observations(FakeClient(),products()[:1])[0]
    assert row.attributes == {"Saveur":"Peach Ice"}
    assert (row.variant_name,row.reference,row.ean) == ("Peach Ice","COMBO","4006381333931")


def test_enrichment_job_is_idempotent_and_persists_summary(tmp_path):
    database=tmp_path/"db.sqlite"; repo=SQLiteOSRepository(database,products()[:1]); key=products()[0].drcloud_product_key
    rows=[ProductObservation(key,"PRESTASHOP","710",variant_name="Peach Ice")]
    first=run_enrichment_job(database,repo,rows,idempotency_key="snapshot:1")
    second=run_enrichment_job(database,repo,rows,idempotency_key="snapshot:1")
    assert first.job_id == second.job_id and first.summary["enriched"] == 1
