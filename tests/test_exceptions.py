import json
from pathlib import Path

from dr_cloud_sync.exceptions import analyse, run


def entry(status="PROBABLE", **source):
    base = {"key": "prestashop:1", "product_id": 1, "combination_id": None,
            "name": "Produit", "ean": "", "reference": "", "price_ttc": 10, "stock": 4}
    return {"prestashop": {**base, **source}, "shopcaisse": None,
            "classification": status, "methode": "NONE"}


def test_probable_unique_ean_becomes_certain():
    report, _ = analyse({"mappings": [entry(ean="123")]},
                        [{"item_id": "x", "name": "Autre", "ean": "123"}])
    assert report["exceptions"][0]["classification_apres"] == "CERTAINE"


def test_import_report_item_id_becomes_certain():
    imports = [{"resultats": [{"statut": "CREATED", "prestashop_key": "prestashop:1",
                                "shopcaisse_id": "made"}]}]
    report, _ = analyse({"mappings": [entry()]}, [{"item_id": "made"}], imports)
    assert report["exceptions"][0]["shopcaisse_item_id"] == "made"


def test_name_and_price_only_stays_probable_and_no_match_is_not_found():
    report, _ = analyse({"mappings": [entry()]},
                        [{"item_id": "x", "name": "Produit", "price_ttc": 10}])
    assert report["exceptions"][0]["classification_apres"] == "PROBABLE"
    report, _ = analyse({"mappings": [entry("NON_TROUVEE")]}, [])
    assert report["exceptions"][0]["classification_apres"] == "NON_TROUVEE"
    assert report["exceptions"][0]["shopcaisse_item_id"] is None


def test_duplicate_identifier_is_never_forced():
    items = [{"item_id": x, "ean": "123"} for x in ("a", "b")]
    report, _ = analyse({"mappings": [entry(ean="123")]}, items)
    assert report["exceptions"][0]["classification_apres"] == "AMBIGUE"


def test_inventory_generated_with_exception_status_and_no_secrets(tmp_path: Path):
    source = tmp_path / "mapping.json"
    source.write_text(json.dumps({"mappings": [entry("NON_TROUVEE")]}))
    run(source, tmp_path, [], [])
    inventory = json.loads((tmp_path / "liste-inventaire-drcloud.json").read_text())
    assert inventory["articles"][0]["mapping_status"] == "NON_TROUVEE"
    outputs = (tmp_path / "rapport-exceptions-mapping.json").read_text() + json.dumps(inventory)
    assert "API_KEY" not in outputs and "Authorization" not in outputs
