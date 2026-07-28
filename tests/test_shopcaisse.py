from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from dr_cloud_sync.shopcaisse import ShopCaisseClient, ShopCaisseError, normalize, reconcile


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_pull_is_get_only_and_paginates_without_exposing_secret():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        path = urlparse(request.full_url).path
        page = int(parse_qs(urlparse(request.full_url).query)["page"][0])
        payloads = {
            "/v1/companies": {0: ({"items": [{"id": "company-1"}], "hasNextPage": True}),
                              1: ({"items": [], "hasNextPage": False})},
            "/v1/stores": {0: {"items": [{"id": "store-1"}], "hasNextPage": False}},
            "/v1/companies/company-1/items": {
                0: {"items": [{"id": "item-1", "companyId": "company-1"}],
                    "hasNextPage": False}},
            "/v1/companies/company-1/prices": {
                0: {"items": [{"id": "prices-1"}], "hasNextPage": False}},
            "/v1/companies/company-1/prices/prices-1": {
                0: {"items": [{"item": "item-1", "price": 12.5}], "hasNextPage": False}},
            "/v1/stores/store-1/stocks": {
                0: {"items": [{"item": "item-1", "stock": 4}], "hasNextPage": False}},
        }
        return Response(payloads[path][page])

    catalogue = ShopCaisseClient("top-secret", page_size=1, opener=opener).pull_catalogue()
    assert catalogue["items"] == [{"id": "item-1", "companyId": "company-1"}]
    assert catalogue["prices"] == [{"item": "item-1", "price": 12.5}]
    assert catalogue["stocks"] == [{"store": "store-1", "item": "item-1", "stock": 4}]
    assert all(call.method == "GET" for call in calls)
    assert all("top-secret" not in call.full_url for call in calls)
    assert all(call.headers["Authorization"] == "Bearer top-secret" for call in calls)
    assert all("pageSize=1" in call.full_url for call in calls)
    assert not any(urlparse(call.full_url).path == "/v1/products" for call in calls)


def test_requires_api_key():
    with pytest.raises(ShopCaisseError, match="absent"):
        ShopCaisseClient("")


def test_rejects_page_size_above_documented_limit():
    with pytest.raises(ShopCaisseError, match="1 et 25"):
        ShopCaisseClient("secret", page_size=26)


def test_normalizes_real_item_price_and_stock_fields():
    result = normalize(
        [{"id": "variation-1", "companyId": "company-1", "name": "Grande",
          "type": "VARIATION", "parentItem": "item-1", "reference": "SKU-1",
          "barcodes": ["3760000000001"], "defaultPrice": 10.0}],
        prices=[{"priceList": "list-1", "item": "variation-1", "price": 11.0}],
        stocks=[{"store": "store-1", "item": "variation-1", "stock": 3}],
    )
    assert result[0]["ean"] == "3760000000001"
    assert result[0]["sku"] == "SKU-1"
    assert result[0]["variation"] == "Grande"
    assert result[0]["parent_item_id"] == "item-1"
    assert result[0]["price"] == 10.0
    assert result[0]["prices"][0]["price"] == 11.0
    assert result[0]["stocks"][0]["store"] == "store-1"


def test_reconciliation_priorities_and_categories():
    shop = normalize([
        {"id": "s1", "ean": "123", "sku": "wrong", "name": "Café"},
        {"id": "s2", "sku": "REF2", "name": "Autre"},
        {"id": "s3", "name": "Produit seul"},
    ])
    report = reconcile(shop, [
        {"id": "p1", "ean13": "123", "reference": "different", "name": "X"},
        {"id": "p2", "reference": "REF2", "name": "Y"},
        {"id": "p3", "name": "Sans rapport"},
    ])
    assert [row["methode"] for row in report["certaines"]] == ["ean_exact", "sku_exact"]
    assert [row["id"] for row in report["uniquement_shopcaisse"]] == ["s3"]
    assert [row["id"] for row in report["uniquement_prestashop"]] == ["p3"]
