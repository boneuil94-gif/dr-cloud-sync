from __future__ import annotations

import json

import pytest

from dr_cloud_sync.cli import main
from dr_cloud_sync.pilot import (
    CreationLimiter, PilotSafetyError, duplicate_candidate, load_manifest, run_pilot,
)


def selection(number=1, *, classification="PRET_A_CREER", ean=None):
    name = f"Article {number}"
    payload = {"name": name, "price": float(number)}
    if ean:
        payload["barcode"] = ean
    return {
        "prestashop_id": number, "combination_id": number + 100,
        "nom_prevu": name, "prix_prevu": float(number), "ean_prevu": ean,
        "reference_prevue": None, "classification_dry_run": classification,
        "shopcaisse_id_dry_run": None, "payload_valide_dry_run": payload,
    }


def write_manifest(path, rows=None):
    path.write_text(json.dumps({"selections": rows or [selection(i) for i in range(1, 6)]}),
                    encoding="utf-8")


class FakeClient:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.calls = []

    def pull_company_items(self, company_id):
        self.calls.append(("GET", company_id))
        return list(self.existing)

    def create_company_item(self, company_id, payload):
        self.calls.append(("POST", company_id, payload))
        item = {"id": f"new-{len([c for c in self.calls if c[0] == 'POST'])}",
                "name": payload["name"], "defaultPrice": payload["price"],
                "barcodes": [payload["barcode"]] if payload.get("barcode") else []}
        self.existing.append(item)
        return item

    def get_company_item(self, company_id, item_id):
        self.calls.append(("GET", company_id, item_id))
        return next(item for item in self.existing if item["id"] == item_id)


def test_pilot_posts_at_most_five_and_gets_immediately_before_each(tmp_path):
    manifest, report = tmp_path / "manifest.json", tmp_path / "report.json"
    write_manifest(manifest)
    client = FakeClient()
    result = run_pilot("secret-not-used-by-fake", "IMPORT-5", "company", manifest, report,
                       client=client)
    assert [row["statut"] for row in result["resultats"]] == ["CREATED"] * 5
    assert len([call for call in client.calls if call[0] == "POST"]) == 5
    assert all(client.calls[index - 1][0] == "GET"
               for index, call in enumerate(client.calls) if call[0] == "POST")
    assert {call[0] for call in client.calls} == {"GET", "POST"}
    assert "secret-not-used-by-fake" not in report.read_text(encoding="utf-8")


def test_sixth_post_is_impossible_even_if_called_directly():
    client = FakeClient()
    limiter = CreationLimiter(client)
    for number in range(5):
        limiter.create("company", {"name": str(number), "price": number})
    with pytest.raises(PilotSafetyError, match="Plafond absolu"):
        limiter.create("company", {"name": "six", "price": 6})
    assert len([call for call in client.calls if call[0] == "POST"]) == 5


def test_duplicate_by_ean_or_normalized_name_is_skipped(tmp_path):
    rows = [selection(i) for i in range(1, 6)]
    rows[0] = selection(1, ean="123")
    manifest, report = tmp_path / "manifest.json", tmp_path / "report.json"
    write_manifest(manifest, rows)
    client = FakeClient([{"id": "old", "name": "autre", "barcodes": ["123"]},
                         {"id": "old-2", "name": "ARTICLE 2"}])
    result = run_pilot("secret", "IMPORT-5", "company", manifest, report, client=client)
    assert [row["statut"] for row in result["resultats"][:2]] == ["SKIPPED", "SKIPPED"]
    assert len([call for call in client.calls if call[0] == "POST"]) == 3


@pytest.mark.parametrize("classification", ["EXISTANTE_PROBABLE", "AMBIGUE", "CONFLIT"])
def test_manifest_refuses_probable_ambiguous_and_conflict(tmp_path, classification):
    rows = [selection(i) for i in range(1, 6)]
    rows[2] = selection(3, classification=classification)
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, rows)
    with pytest.raises(PilotSafetyError, match="PRET_A_CREER"):
        load_manifest(manifest)


def test_incorrect_confirmation_performs_zero_network_or_write(tmp_path):
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest)
    client = FakeClient()
    with pytest.raises(PilotSafetyError, match="zéro écriture"):
        run_pilot("secret", "import-5", "company", manifest, tmp_path / "report.json",
                  client=client)
    assert client.calls == []


def test_dry_run_shopcaisse_id_is_always_refused(tmp_path):
    rows = [selection(i) for i in range(1, 6)]
    rows[0]["shopcaisse_id_dry_run"] = "existing"
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, rows)
    with pytest.raises(PilotSafetyError, match="correspond déjà"):
        load_manifest(manifest)


def test_duplicate_candidate_compares_available_reference():
    row = selection(1)
    row["reference_prevue"] = "REAL-REF"
    assert duplicate_candidate([{"id": "old", "reference": "REAL-REF"}], row)["id"] == "old"


def test_cli_returns_failure_when_any_pilot_result_failed(monkeypatch, tmp_path):
    report = {"resultats": [{"statut": "CREATED"}, {"statut": "FAILED"}]}
    monkeypatch.setattr("dr_cloud_sync.cli.run_pilot", lambda *args, **kwargs: report)
    monkeypatch.chdir(tmp_path)
    assert main(["shopcaisse-import-pilot"]) == 1


def test_cli_accepts_created_and_duplicate_skips(monkeypatch, tmp_path):
    report = {"resultats": [{"statut": "CREATED"}, {"statut": "SKIPPED"}]}
    monkeypatch.setattr("dr_cloud_sync.cli.run_pilot", lambda *args, **kwargs: report)
    monkeypatch.chdir(tmp_path)
    assert main(["shopcaisse-import-pilot"]) == 0
