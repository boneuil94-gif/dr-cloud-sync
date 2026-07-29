import json

import pytest

from dr_cloud_sync.final_mapping import FinalMappingError, finalize_mapping


def dump(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class ReadOnlyClient:
    def __init__(self, missing=None):
        self.gets = []
        self.missing = missing

    def get_company_item(self, company, item_id):
        self.gets.append((company, item_id))
        if item_id == self.missing:
            raise ValueError("absent")
        number = item_id.split("-")[-1]
        return {"id": item_id, "name": f"Produit {number}", "barcode": f"EAN{number}", "stock": 2}


@pytest.fixture
def dataset(tmp_path):
    initial = [{"prestashop": {"key": f"old:{i}", "product_id": i, "combination_id": 0,
                               "name": f"Ancien {i}", "ean": "", "reference": f"R{i}", "stock": 1},
                "shopcaisse": {"item_id": f"old-item-{i}"}, "classification": "CERTAINE",
                "methode": "EAN", "preuve": "EAN exact"} for i in range(444)]
    exceptions = [{"prestashop": {"key": f"new:{i}", "product_id": 500 + i,
                                   "combination_id": i, "name": f"Produit {i}",
                                   "ean": f"EAN{i}", "reference": f"N{i}", "stock": 3},
                   "shopcaisse": None, "classification": "NON_TROUVEE", "methode": "NONE"}
                  for i in range(34)]
    mapping, corrections, creation = (tmp_path / name for name in ("mapping.json", "corrections.json", "creation.json"))
    dump(mapping, {"prestashop_total": 478, "mappings": initial + exceptions})
    dump(corrections, {"version": 1, "corrections": {
        f"new:{i}": {"shopcaisse_item_id": f"new-item-{i}", "source": "EXCEPTION_REBUILD"}
        for i in range(34)}})
    dump(creation, {"attendues": 34, "created": 34, "skipped_already_created": 0,
                    "failed": 0, "complete": True})
    return mapping, corrections, creation, tmp_path


def run(dataset, client=None):
    return finalize_mapping(*dataset[:3], dataset[3], "company", client or ReadOnlyClient())


def test_valid_444_plus_34_builds_mapping_and_inventory(dataset):
    client = ReadOnlyClient()
    report = run(dataset, client)
    assert report["mapping_total"] == report["certaine"] == 478
    assert report["shopcaisse_corrections_revalidated"] == len(client.gets) == 34
    assert report["ready_for_inventory"] is True
    mapping = json.loads((dataset[3] / "mapping-prestashop-shopcaisse-final.json").read_text())
    assert len(mapping["mappings"]) == 478
    assert all(row["methode"] == "EXCEPTION_REBUILD" for row in mapping["mappings"][444:])
    inventory = json.loads((dataset[3] / "inventaire-initial-drcloud.json").read_text())
    assert len(inventory) == 478
    assert all(row["quantite_physique"] is None and row["inventaire_valide"] is False for row in inventory)
    assert not any(hasattr(client, method) for method in ("post", "put", "patch", "delete"))


@pytest.mark.parametrize("mutation", [
    lambda value: value["corrections"].pop("new:0"),
    lambda value: value["corrections"]["new:0"].update(shopcaisse_item_id=""),
    lambda value: value["corrections"].update({"old:0": value["corrections"].pop("new:0")}),
])
def test_bad_correction_fails_and_report_is_not_ready(dataset, mutation):
    value = json.loads(dataset[1].read_text())
    mutation(value)
    dump(dataset[1], value)
    with pytest.raises(FinalMappingError):
        run(dataset)
    report = json.loads((dataset[3] / "rapport-mapping-final.json").read_text())
    assert report["ready_for_inventory"] is False


def test_duplicate_prestashop_key_fails(dataset):
    value = json.loads(dataset[0].read_text())
    value["mappings"][1]["prestashop"]["key"] = "old:0"
    dump(dataset[0], value)
    with pytest.raises(FinalMappingError):
        run(dataset)


def test_missing_shopcaisse_correction_fails(dataset):
    with pytest.raises(ValueError, match="absent"):
        run(dataset, ReadOnlyClient("new-item-0"))
    assert json.loads((dataset[3] / "rapport-mapping-final.json").read_text())["ready_for_inventory"] is False


def test_duplicate_item_id_is_reported_and_blocks_ready(dataset):
    value = json.loads(dataset[1].read_text())
    value["corrections"]["new:1"]["shopcaisse_item_id"] = "new-item-0"
    # Store explicit expected values: both GETs legitimately refer to the same item.
    value["corrections"]["new:1"].update(name="Produit 0", ean="EAN0")
    dump(dataset[1], value)
    with pytest.raises(FinalMappingError, match="partagé"):
        run(dataset)
    report = json.loads((dataset[3] / "rapport-mapping-final.json").read_text())
    assert report["duplicate_shopcaisse_item_ids"] == ["new-item-0"]
    assert report["ready_for_inventory"] is False


def test_creation_report_must_be_complete(dataset):
    value = json.loads(dataset[2].read_text())
    value.update(created=33, failed=1, complete=False)
    dump(dataset[2], value)
    with pytest.raises(FinalMappingError):
        run(dataset)


def test_artifacts_do_not_contain_secrets(dataset):
    run(dataset)
    content = "".join(path.read_text() for path in dataset[3].glob("*.json"))
    assert "SHOPCAISSE_API_KEY" not in content and "secret-api-value" not in content
