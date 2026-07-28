import json
from urllib.request import Request

import pytest

from dr_cloud_sync.mapping import build_mapping, prestashop_key
from dr_cloud_sync.prestashop import PrestaShopClient
from dr_cloud_sync.shopcaisse import ShopCaisseClient


def ps(**values):
    return {"product_id": 1, "combination_id": None, "name": "Crème Test",
            "ean": "", "reference": "", "price_ttc": 12.0, "stock": 3, **values}


def sc(item_id="item-1", **values):
    return {"item_id": item_id, "name": "Crème Test", "ean": "", "reference": "",
            "price_ttc": 12.0, **values}


def result(source, targets, reports=()):
    return build_mapping([source], targets, reports)[0]["mappings"][0]


def test_created_import_report_is_certain_and_preserves_item_id():
    report = {"resultats": [{"statut": "CREATED", "prestashop_key": "prestashop:1",
                              "shopcaisse_id": "created-id"}]}
    mapping = result(ps(), [sc("created-id")], [report])
    assert (mapping["classification"], mapping["methode"]) == ("CERTAINE", "IMPORT_REPORT")
    assert mapping["shopcaisse"]["item_id"] == "created-id"


@pytest.mark.parametrize(("field", "method"), [("ean", "EAN"), ("reference", "REFERENCE")])
def test_unique_identifier_is_certain(field, method):
    mapping = result(ps(**{field: "ABC-123"}), [sc(**{field: "ABC123"})])
    assert (mapping["classification"], mapping["methode"]) == ("CERTAINE", method)


@pytest.mark.parametrize(("field", "method"),
                         [("ean", "EAN_DUPLICATE"), ("reference", "REFERENCE_DUPLICATE")])
def test_duplicate_identifier_is_ambiguous(field, method):
    mapping = result(ps(**{field: "same"}), [sc("a", **{field: "same"}), sc("b", **{field: "same"})])
    assert (mapping["classification"], mapping["methode"]) == ("AMBIGUE", method)


def test_name_and_price_is_never_certain():
    assert result(ps(), [sc()])["classification"] == "PROBABLE"


def test_conflicting_ean_and_reference():
    mapping = result(ps(ean="111", reference="REF"),
                     [sc("ean-item", ean="111"), sc("ref-item", reference="REF")])
    assert mapping["classification"] == "CONFLIT"


def test_no_candidate():
    assert result(ps(name="introuvable"), [sc(name="autre")])["classification"] == "NON_TROUVEE"


def test_deterministic_prestashop_identifier():
    assert prestashop_key(123) == "prestashop:123"
    assert prestashop_key(123, 456) == "prestashop:123:456"


def test_clients_catalogue_requests_are_get_only():
    requests: list[Request] = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"items": [], "hasNextPage": false}'

    def opener(request, **_kwargs):
        requests.append(request)
        return Response()

    ShopCaisseClient("not-a-secret", opener=opener).pull_company_items("company")
    list(PrestaShopClient("https://example.test/api", "not-a-secret", opener=opener).iter_resource("products"))
    assert requests and {request.get_method() for request in requests} == {"GET"}


def test_outputs_contain_no_credentials():
    secret = "super-secret-api-key"
    mapping, report = build_mapping([ps()], [sc()])
    assert secret not in json.dumps([mapping, report])


def test_quality_report_lists_every_non_certain_mapping():
    _, report = build_mapping([ps(name="missing")], [])
    assert report["non_trouvee"] == 1
    assert report["details"]["NON_TROUVEE"][0]["prestashop"]["key"] == "prestashop:1"
