from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from dr_cloud_sync.rehydration import packaged_historical_snapshot
from dr_cloud_sync.shopcaisse import (
    ShopCaisseClient, ShopCaisseError, build_import_dry_run, extract_prestashop, normalize, reconcile,
)


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
    assert [row["methode"] for row in report["certaines"]] == ["ean_exact", "reference_exacte"]
    assert [row["id"] for row in report["uniquement_shopcaisse"]] == ["s3"]
    assert [row["id"] for row in report["uniquement_prestashop"]] == ["p3"]


def test_real_prestashop_snapshot_extracts_every_combination_and_plain_product():
    with open(packaged_historical_snapshot(), encoding="utf-8") as stream:
        entries, counts = extract_prestashop(json.load(stream))
    assert counts == {"products": 72, "combinations": 453, "comparable_entries": 478}
    assert sum(row["combination_id"] is not None for row in entries) == 453
    celeste = next(row for row in entries if row["combination_id"] == 54)
    assert celeste["product_id"] == 22
    assert celeste["product_name"] == "Chicha CELESTE ®"
    assert celeste["color"] == "NARDO GREY"
    assert celeste["stock"] == 1


def test_empty_identifiers_are_never_matches_and_leave_prestashop_entries():
    report = reconcile(normalize([{"id": "s", "name": "sans rapport"}]), {
        "catalogue": [{"id": 1, "nom": "autre", "ean": None,
                       "reference": None, "declinaisons": []}]
    })
    assert not report["certaines"]
    assert report["uniquement_shopcaisse"][0]["id"] == "s"
    assert report["uniquement_prestashop"][0]["product_id"] == 1


def test_parent_child_and_product_color_size_match():
    shop = normalize([
        {"id": "parent", "name": "Chicha Celeste", "type": "PRODUCT"},
        {"id": "child", "name": "Nardo Grey", "type": "VARIATION",
         "parentItem": "parent"},
    ])
    assert shop[1]["product"] == "Chicha Celeste"
    assert shop[1]["variation"] == "Nardo Grey"
    report = reconcile([shop[1]], {"catalogue": [{
        "id": 22, "nom": "Chicha Celeste", "declinaisons": [{
            "id": 54, "attributs": [{"nom": "NARDO GREY", "groupe": "Couleurs"}],
            "ean": None, "reference": None, "stock": 1,
        }],
    }]})
    assert report["probables"][0]["methode"] == "produit_couleur_taille_identiques"


def test_ambiguity_keeps_all_equivalent_candidates():
    report = reconcile(normalize([{"id": "s", "name": "Même produit"}]), {
        "catalogue": [
            {"id": 1, "nom": "Même produit", "declinaisons": []},
            {"id": 2, "nom": "Même produit", "declinaisons": []},
        ]
    })
    assert len(report["ambigues"]) == 1
    assert len(report["ambigues"][0]["candidats"]) == 2
    assert report["uniquement_prestashop"] == []


def test_import_dry_run_generates_no_network_operation_and_blocks_missing_price():
    raw = {"items": [], "prices": [], "stocks": [], "companies": [{"id": "c"}],
           "stores": [{"id": "s"}], "priceLists": []}
    presta = {"catalogue": [{"id": 1, "nom": "Produit", "ean": None,
                              "reference": None, "declinaisons": []}]}
    plan, payloads, report = build_import_dry_run(raw, presta)
    assert plan["entrees"][0]["action_prevue"] == "DONNEES_MANQUANTES"
    assert payloads["operations"][0]["statut"] == "BLOQUE"
    assert payloads["operations"][0]["payload"] is None
    assert payloads["operations"][0]["champs_obligatoires_manquants"] == ["price"]
    assert report["données manquantes"] == 1


def test_dry_run_declares_only_documented_write_methods_without_executing_them():
    _, payloads, _ = build_import_dry_run(
        {"items": [], "prices": [], "stocks": [], "companies": [], "stores": [], "priceLists": []},
        {"catalogue": []},
    )
    assert {e["method"] for e in payloads["openapi"]["endpoints_ecriture_identifies"]} == {"POST", "PUT", "DELETE"}
    assert payloads["operations"] == []


def test_valid_price_is_ready_and_attributes_are_flattened_without_invention():
    raw = {"items": [], "prices": [], "stocks": [], "companies": [{"id": "company-1"}],
           "stores": [{"id": "store-1"}], "priceLists": []}
    presta = {"catalogue": [{"id": 22, "nom": "Chicha CELESTE", "declinaisons": [{
        "id": 54, "attributs": [{"nom": "NARDO GREY", "groupe": "Couleurs"}],
        "ean": None, "reference": None, "stock": 1,
    }]}]}
    fields = {"22:54": {"price_ttc": 149.0, "price_ht": 124.166667,
                         "product_price_ht": 124.166667,
                         "combination_price_impact_ht": 0.0,
                         "price_source": "PrestaShop GET", "currency": None}}
    plan, payloads, report = build_import_dry_run(raw, presta, fields)
    assert plan["entrees"][0]["action_prevue"] == "PRET_A_CREER"
    assert plan["entrees"][0]["attributs_traduits"] is True
    assert payloads["operations"][0]["payload"] == {
        "name": "Chicha CELESTE - NARDO GREY", "price": 149.0,
    }
    assert payloads["operations"][0]["validation_locale"] == {"valide": True, "erreurs": []}
    assert report["prêts à créer"] == 1
