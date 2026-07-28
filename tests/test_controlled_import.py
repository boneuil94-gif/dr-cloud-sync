from __future__ import annotations

import json

import pytest

from dr_cloud_sync.cli import main
from dr_cloud_sync.controlled_import import MAX_CREATIONS, run_controlled_import
from dr_cloud_sync.pilot import PilotSafetyError
from dr_cloud_sync.shopcaisse import ShopCaisseError


EAN = "4006381333931"


def entry(number, *, barcode=None, payload=None):
    fields = payload if payload is not None else {"name": f"Article {number}", "price": float(number)}
    if barcode:
        fields["barcode"] = barcode
    return {"product_id": number, "combination_id": number + 100,
            "reference": f"REF-{number}", "action_prevue": "PRET_A_CREER",
            "champs_qui_seraient_crees": fields}


def write_plan(path, rows):
    path.write_text(json.dumps({"mode": "DRY_RUN_SANS_ECRITURE", "entrees": rows}),
                    encoding="utf-8")


class FakeClient:
    def __init__(self, existing=None, *, mismatch=None, fail_post_at=None):
        self.items = list(existing or [])
        self.calls = []
        self.mismatch = mismatch
        self.fail_post_at = fail_post_at

    def pull_company_items(self, company_id):
        self.calls.append(("GET", f"/v1/companies/{company_id}/items"))
        return list(self.items)

    def create_company_item(self, company_id, payload):
        self.calls.append(("POST", f"/v1/companies/{company_id}/items", payload))
        post_count = sum(call[0] == "POST" for call in self.calls)
        if post_count == self.fail_post_at:
            raise ShopCaisseError("échec nettoyé")
        item = {"id": f"new-{post_count}", "name": payload["name"],
                "defaultPrice": payload["price"],
                "barcodes": [payload["barcode"]] if payload.get("barcode") else []}
        self.items.append(item)
        return item

    def get_company_item(self, company_id, item_id):
        self.calls.append(("GET", f"/v1/companies/{company_id}/items/{item_id}"))
        item = dict(next(row for row in self.items if row["id"] == item_id))
        if self.mismatch == "name":
            item["name"] += " modifié"
        elif self.mismatch == "price":
            item["defaultPrice"] += 1
        elif self.mismatch == "barcode":
            item["barcodes"] = ["9780201379624"]
        return item


def run(tmp_path, rows, client, **credentials):
    plan, report = tmp_path / "plan.json", tmp_path / "report.json"
    write_plan(plan, rows)
    values = {"api_key": "shop-secret", "prestashop_api_key": "presta-secret",
              "confirm": "IMPORT-20", "company_id": "company"}
    values.update(credentials)
    return run_controlled_import(**values, plan_path=plan, report_path=report, client=client)


def test_absolute_maximum_twenty_posts_with_more_candidates(tmp_path):
    client = FakeClient()
    report = run(tmp_path, [entry(i) for i in range(1, 26)], client)
    assert MAX_CREATIONS == 20
    assert report["creations_effectuees"] == 20
    assert len([call for call in client.calls if call[0] == "POST"]) == 20


def test_skipped_does_not_consume_quota_and_existing_is_never_recreated(tmp_path):
    existing = [{"id": f"old-{i}", "name": f"Article {i}", "defaultPrice": float(i)}
                for i in range(1, 6)]
    client = FakeClient(existing)
    report = run(tmp_path, [entry(i) for i in range(1, 26)], client)
    posted_names = [call[2]["name"] for call in client.calls if call[0] == "POST"]
    assert report["skipped"] == 5
    assert report["creations_effectuees"] == 20
    assert all(f"Article {i}" not in posted_names for i in range(1, 6))


def test_stops_immediately_at_first_failed_post(tmp_path):
    client = FakeClient(fail_post_at=3)
    report = run(tmp_path, [entry(i) for i in range(1, 10)], client)
    assert len([call for call in client.calls if call[0] == "POST"]) == 3
    assert report["creations_effectuees"] == 2
    assert report["failed"] == 1 and report["arret_sur_echec"] is True
    assert [row["statut"] for row in report["resultats"]] == ["CREATED", "CREATED", "FAILED"]


def test_created_requires_successful_reread(tmp_path):
    client = FakeClient()
    report = run(tmp_path, [entry(1)], client)
    assert report["resultats"][0]["statut"] == "CREATED"
    assert report["resultats"][0]["verification"] == {
        "name": True, "price": True, "barcode": True}
    assert [call[0] for call in client.calls] == ["GET", "POST", "GET"]


@pytest.mark.parametrize("field", ["name", "price", "barcode"])
def test_reread_difference_is_failed_and_stops(tmp_path, field):
    client = FakeClient(mismatch=field)
    report = run(tmp_path, [entry(1, barcode=EAN), entry(2)], client)
    result = report["resultats"][0]
    assert result["statut"] == "FAILED"
    assert result["verification"][field] is False
    assert len([call for call in client.calls if call[0] == "POST"]) == 1


def test_only_post_write_method_and_endpoint_are_reachable(tmp_path):
    client = FakeClient()
    run(tmp_path, [entry(1)], client)
    writes = [call for call in client.calls if call[0] != "GET"]
    assert [(call[0], call[1]) for call in writes] == [
        ("POST", "/v1/companies/company/items")]
    assert not {"PUT", "PATCH", "DELETE"} & {call[0] for call in client.calls}


@pytest.mark.parametrize("payload", [
    {"name": "", "price": 1.0}, {"name": "Article", "price": None},
    {"name": "Article", "price": float("inf")},
    {"name": "Article", "price": 1.0, "barcode": "123"},
    {"name": "Article", "price": 1.0, "unexpected": "field"},
])
def test_invalid_payload_never_posts(tmp_path, payload):
    client = FakeClient()
    report = run(tmp_path, [entry(1, payload=payload)], client)
    assert report["failed"] == 1
    assert not [call for call in client.calls if call[0] == "POST"]


@pytest.mark.parametrize("missing", ["api_key", "prestashop_api_key", "company_id"])
def test_missing_secret_or_company_performs_no_network_or_write(tmp_path, missing):
    client = FakeClient()
    with pytest.raises(PilotSafetyError, match="zéro écriture"):
        run(tmp_path, [entry(1)], client, **{missing: ""})
    assert client.calls == []


def test_cli_returns_nonzero_when_controlled_result_failed(monkeypatch, tmp_path):
    monkeypatch.setattr("dr_cloud_sync.cli.run_controlled_import", lambda *args, **kwargs: {
        "failed": 1, "resultats": [{"statut": "FAILED"}]})
    monkeypatch.chdir(tmp_path)
    assert main(["shopcaisse-import-controlled"]) == 1
