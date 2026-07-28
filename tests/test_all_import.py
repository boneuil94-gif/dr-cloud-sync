from __future__ import annotations

import json

import pytest

from dr_cloud_sync.cli import main
from dr_cloud_sync.controlled_import import run_all_import
from dr_cloud_sync.pilot import PilotSafetyError
from test_controlled_import import EAN, FakeClient, entry, write_plan


def run(tmp_path, rows, client, **credentials):
    plan, report_path = tmp_path / "plan.json", tmp_path / "report.json"
    write_plan(plan, rows)
    values = {"api_key": "shop-secret", "prestashop_api_key": "presta-secret",
              "confirm": "IMPORT-ALL", "company_id": "company"}
    values.update(credentials)
    report = run_all_import(**values, plan_path=plan, report_path=report_path, client=client)
    assert json.loads(report_path.read_text()) == report
    return report


def test_all_candidates_have_no_twenty_limit_and_finish(tmp_path):
    client = FakeClient()
    report = run(tmp_path, [entry(i) for i in range(1, 31)], client)
    assert report["candidats"] == report["creations_effectuees"] == 30
    assert report["termine"] is True and report["failed"] == 0


def test_twenty_five_existing_are_skipped_without_posts(tmp_path):
    existing = [{"id": f"old-{i}", "name": f"Article {i}", "defaultPrice": float(i)}
                for i in range(1, 26)]
    client = FakeClient(existing)
    report = run(tmp_path, [entry(i) for i in range(1, 26)], client)
    assert report["skipped"] == 25 and report["creations_effectuees"] == 0
    assert not [call for call in client.calls if call[0] == "POST"]


def test_local_antiduplicate_state_is_updated_immediately(tmp_path):
    client = FakeClient()
    report = run(tmp_path, [entry(1), entry(1)], client)
    assert [row["statut"] for row in report["resultats"]] == ["CREATED", "SKIPPED"]
    assert len([call for call in client.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize("payload", [
    {"name": "", "price": 1.0}, {"name": "Article", "price": None},
    {"name": "Article", "price": float("inf")},
    {"name": "Article", "price": 1.0, "barcode": "123"},
    {"name": "Article", "price": 1.0, "forbidden": True},
])
def test_invalid_payload_fails_before_post_and_stops(tmp_path, payload):
    client = FakeClient()
    report = run(tmp_path, [entry(1, payload=payload), entry(2)], client)
    assert report["failed"] == 1 and report["arret_sur_echec"] is True
    assert report["termine"] is False and len(report["resultats"]) == 1
    assert not [call for call in client.calls if call[0] == "POST"]


@pytest.mark.parametrize("field", ["name", "price", "barcode"])
def test_reread_mismatch_fails_and_no_later_post(tmp_path, field):
    client = FakeClient(mismatch=field)
    report = run(tmp_path, [entry(1, barcode=EAN), entry(2)], client)
    assert report["resultats"][0]["verification"][field] is False
    assert report["failed"] == 1 and report["termine"] is False
    assert len([call for call in client.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize("missing", ["api_key", "prestashop_api_key", "company_id"])
def test_missing_configuration_has_zero_network_calls(tmp_path, missing):
    client = FakeClient()
    with pytest.raises(PilotSafetyError, match="zéro écriture"):
        run(tmp_path, [entry(1)], client, **{missing: ""})
    assert client.calls == []


def test_wrong_confirmation_has_zero_network_calls(tmp_path):
    client = FakeClient()
    with pytest.raises(PilotSafetyError, match="zéro écriture"):
        run(tmp_path, [entry(1)], client, confirm="no")
    assert client.calls == []


def test_created_reread_and_only_authorized_write(tmp_path):
    client = FakeClient()
    report = run(tmp_path, [entry(1, barcode=EAN)], client)
    assert report["resultats"][0]["statut"] == "CREATED"
    assert all(report["resultats"][0]["verification"].values())
    writes = [call for call in client.calls if call[0] != "GET"]
    assert [(call[0], call[1]) for call in writes] == [("POST", "/v1/companies/company/items")]
    assert not {"PUT", "PATCH", "DELETE"} & {call[0] for call in client.calls}


def test_cli_returns_nonzero_on_failed(monkeypatch, tmp_path):
    monkeypatch.setattr("dr_cloud_sync.cli.run_all_import", lambda *args, **kwargs: {
        "failed": 1, "resultats": [{"statut": "FAILED"}]})
    monkeypatch.chdir(tmp_path)
    assert main(["shopcaisse-import-all"]) == 1
