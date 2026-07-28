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
        page = int(parse_qs(urlparse(request.full_url).query)["page"][0])
        return Response({"products": [{"id": page}] if page < 3 else []})

    products = ShopCaisseClient("top-secret", page_size=1, opener=opener).pull_products()
    assert products == [{"id": 1}, {"id": 2}]
    assert all(call.method == "GET" for call in calls)
    assert all("top-secret" not in call.full_url for call in calls)
    assert all(call.headers["Authorization"] == "Bearer top-secret" for call in calls)


def test_requires_api_key():
    with pytest.raises(ShopCaisseError, match="absent"):
        ShopCaisseClient("")


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
