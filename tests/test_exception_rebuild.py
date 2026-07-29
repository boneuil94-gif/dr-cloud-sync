import json
from pathlib import Path

import pytest

from dr_cloud_sync.exception_rebuild import (build_final_mapping, load_exceptions,
                                              run_exception_rebuild)
from dr_cloud_sync.pilot import PilotSafetyError


def exception_rows():
    return [
        {"prestashop_key": f"prestashop:{i}:{100+i}", "product_id": i,
         "combination_id": 100 + i, "classification_apres": "PROBABLE" if i < 14 else "NON_TROUVEE"}
        for i in range(34)
    ]


def units():
    return [{"key": row["prestashop_key"], "product_id": row["product_id"],
             "combination_id": row["combination_id"], "name": "Produit",
             "attributes": [f"Taille {row['combination_id']}"], "price_ttc": 12.5,
             "ean": "4006381333931", "reference": f"R{row['combination_id']}"}
            for row in exception_rows()]


def write_json(path: Path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeClient:
    def __init__(self, mismatch=None):
        self.posts = []
        self.gets = []
        self.mismatch = mismatch

    def create_company_item(self, company_id, payload):
        self.posts.append((company_id, payload))
        return {"id": str(len(self.posts))}

    def get_company_item(self, company_id, item_id):
        self.gets.append((company_id, item_id))
        payload = dict(self.posts[int(item_id) - 1][1])
        result = {"name": payload["name"], "defaultPrice": payload["price"],
                  "barcodes": [payload["barcode"]] if "barcode" in payload else []}
        if self.mismatch == "name": result["name"] = "autre"
        if self.mismatch == "price": result["defaultPrice"] = 99
        if self.mismatch == "barcode": result["barcodes"] = ["12345670"]
        return result


@pytest.fixture
def paths(tmp_path):
    exceptions, corrections, report = (tmp_path / name for name in ("exceptions.json", "corrections.json", "report.json"))
    write_json(exceptions, {"exceptions": exception_rows()})
    return exceptions, corrections, report


def invoke(paths, client, source=None, **credentials):
    return run_exception_rebuild(credentials.get("api_key", "sc"), credentials.get("ps_key", "ps"),
                                 credentials.get("confirm", "CREATE-34"), credentials.get("company", "company"),
                                 *paths, prestashop_loader=lambda _url: units() if source is None else source,
                                 client=client)


def test_exact_selection_includes_only_two_classes(paths):
    rows = exception_rows() + [{"prestashop_key": "certain", "classification_apres": "CERTAINE"}]
    write_json(paths[0], {"exceptions": rows})
    selected = load_exceptions(paths[0])
    assert len(selected) == 34
    assert sum(x["classification_apres"] == "PROBABLE" for x in selected) == 14
    assert sum(x["classification_apres"] == "NON_TROUVEE" for x in selected) == 20
    assert all(x["classification_apres"] != "CERTAINE" for x in selected)


def test_wrong_total_stops_before_post(paths):
    write_json(paths[0], {"exceptions": exception_rows()[:-1]})
    client = FakeClient()
    with pytest.raises(PilotSafetyError):
        invoke(paths, client)
    assert client.posts == []


def test_wrong_confirmation_stops_before_report_loading_or_post(paths):
    paths[0].write_text("invalid json", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(PilotSafetyError, match="Confirmation incorrecte"):
        invoke(paths, client, confirm="NO")
    assert client.posts == []


@pytest.mark.parametrize("url", ["relative/api", "/api"])
def test_invalid_url_stops_before_prestashop_network_or_post(paths, url):
    client = FakeClient()
    loaded = []
    with pytest.raises(ValueError, match="URL API absolue requise"):
        run_exception_rebuild("sc", "ps", "CREATE-34", "company", *paths,
                              prestashop_api_url=url,
                              prestashop_loader=lambda resolved: loaded.append(resolved) or [],
                              client=client)
    assert loaded == []
    assert client.posts == []


def test_prestashop_failure_stops_before_shopcaisse_post(paths):
    client = FakeClient()

    def fail(_url):
        raise RuntimeError("PrestaShop unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        run_exception_rebuild("sc", "ps", "CREATE-34", "company", *paths,
                              prestashop_loader=fail, client=client)
    assert client.posts == []


def test_explicit_valid_url_is_passed_to_prestashop_loader(paths):
    client = FakeClient()
    received = []

    def load(url):
        received.append(url)
        raise RuntimeError("stop after URL check")

    with pytest.raises(RuntimeError, match="URL check"):
        run_exception_rebuild("sc", "ps", "CREATE-34", "company", *paths,
                              prestashop_api_url="https://example.test/api/",
                              prestashop_loader=load, client=client)
    assert received == ["https://example.test/api"]
    assert client.posts == []


@pytest.mark.parametrize("missing", ["api_key", "ps_key", "company"])
def test_missing_configuration_stops_before_loading_or_post(paths, missing):
    client = FakeClient()
    values = {missing: ""}
    with pytest.raises(PilotSafetyError):
        invoke(paths, client, **values)
    assert client.posts == []


def test_creates_each_combination_separately_and_maximum_34(paths):
    client = FakeClient()
    report = invoke(paths, client)
    assert report["created"] == len(client.posts) == len(client.gets) == 34
    assert len({payload["name"] for _, payload in client.posts}) == 34
    assert report["complete"] is True
    # There is deliberately no catalogue/name-price duplicate lookup.
    assert all(set(payload) == {"name", "price", "barcode", "reference"} for _, payload in client.posts)


def test_restart_skips_every_persisted_key(paths):
    first = FakeClient()
    invoke(paths, first)
    second = FakeClient()
    report = invoke(paths, second)
    assert not second.posts
    assert report["skipped_already_created"] == 34
    corrections = json.loads(paths[1].read_text())["corrections"]
    assert len(corrections) == 34
    assert all(value["source"] == "EXCEPTION_REBUILD" for value in corrections.values())


@pytest.mark.parametrize("field", ["name", "price", "barcode"])
def test_reread_mismatch_fails_and_stops(field, paths):
    client = FakeClient(field)
    report = invoke(paths, client)
    assert report["failed"] == 1
    assert len(client.posts) == len(client.gets) == 1
    assert report["resultats"][0]["verification"][field] is False
    assert json.loads(paths[1].read_text())["corrections"] == {}


@pytest.mark.parametrize("bad", [{"name": "", "price_ttc": 2}, {"name": "x", "price_ttc": None}])
def test_invalid_payload_posts_nothing_and_stops(paths, bad):
    source = units()
    source[0].update(bad)
    client = FakeClient()
    report = invoke(paths, client, source=source)
    assert report["failed"] == 1
    assert client.posts == []


def test_only_post_and_get_client_surface_is_used(paths):
    client = FakeClient()
    invoke(paths, client)
    assert not any(hasattr(client, method) for method in ("put", "patch", "delete"))
    serialized = paths[1].read_text() + paths[2].read_text()
    assert "sc" not in serialized and "ps" not in serialized


def test_final_mapping_merges_444_and_34(tmp_path, paths):
    original_rows = [
        {"prestashop": {"key": f"prestashop:certain:{i}"}, "shopcaisse": {"item_id": f"old-{i}"},
         "classification": "CERTAINE", "methode": "EAN", "confidence": 1.0}
        for i in range(444)
    ] + [
        {"prestashop": {"key": row["prestashop_key"]}, "shopcaisse": None,
         "classification": row["classification_apres"], "methode": "NONE", "confidence": 0}
        for row in exception_rows()
    ]
    mapping = tmp_path / "mapping.json"
    output = tmp_path / "final.json"
    write_json(mapping, {"prestashop_total": 478, "mappings": original_rows})
    corrections = {row["prestashop_key"]: {"shopcaisse_item_id": f"new-{i}",
                   "created_at": "now", "source": "EXCEPTION_REBUILD"}
                   for i, row in enumerate(exception_rows())}
    write_json(paths[1], {"version": 1, "corrections": corrections})
    final = build_final_mapping(mapping, paths[0], paths[1], output)
    assert final["complete"] is True
    assert (final["certaine"], final["probable"], final["ambigue"],
            final["non_trouvee"], final["conflit"]) == (478, 0, 0, 0, 0)
    assert len({x["shopcaisse"]["item_id"] for x in final["mappings"]}) == 478
