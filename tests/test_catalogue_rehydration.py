import json

import pytest

from dr_cloud_sync.domain import Product, StockMovement, MovementType, MovementStatus
from dr_cloud_sync.hydration import ProductObservation
from dr_cloud_sync.rehydration import (AMBIGUOUS, SAFE, CatalogueRehydrationService,
                                        historical_observations, packaged_historical_snapshot)
from dr_cloud_sync.repositories import SQLiteOSRepository, SQLiteStockMovementRepository


NAMES = ["PEACH ICE", "STRAWBERRY PUNCH", "LOVE", "GUM MINT", "MINT", "MAGIC LOVE", "MANGO PINEAPPLE"]


def product(cid, **kw):
    return Product(f"prestashop:100:{cid}", "100", 100, cid, f"sc-{cid}",
                   "AL FAKHER CROWN BAR Hyper Max Prime 50K", **kw)


def snapshot(tmp_path):
    path = tmp_path / "catalogue-prestashop-reconstruit.json"
    path.write_text(json.dumps({"catalogue": [{"id": 100, "nom": "AL FAKHER CROWN BAR Hyper Max Prime 50K",
      "declinaisons": [{"id": 710+i, "ean": None, "reference": None,
        "attributs": [{"id": 455+i, "nom": name, "groupe_id": 40, "groupe": "AL FAKHER 50K"}]}
       for i, name in enumerate(NAMES)]}]}))
    return path


def test_historical_snapshot_hyper_max_preview_and_apply_is_idempotent(tmp_path):
    products = [product(710+i) for i in range(7)]
    repo = SQLiteOSRepository(tmp_path / "db.sqlite", products)
    observations = historical_observations(snapshot(tmp_path), repo.all())
    backups = []
    service = CatalogueRehydrationService(repo, backup=lambda: backups.append("backup") or "backup")
    preview = service.preview(observations)
    assert preview["summary"] == {"total": 7, "processed": 7, "safe": 7, "ambiguous": 0,
                                  "no_data": 0, "products_to_change": 7, "fields_to_change": 14}
    assert [x["candidates"]["variant_name"] for x in preview["safe"]] == NAMES
    assert all(x["fields"]["ean"] == "NO_DATA" and x["fields"]["reference"] == "NO_DATA" for x in preview["safe"])
    identities = [p.drcloud_product_key for p in repo.all()]
    first = service.apply_safe(observations, actor="admin")
    second = service.apply_safe(observations, actor="admin")
    assert first["changed"] == 7 and second["changed"] == 0 and len(backups) == 2
    assert [p.variant_name for p in repo.all()] == NAMES
    assert repo.get(identities[0]).display_name == "AL FAKHER CROWN BAR Hyper Max Prime 50K — PEACH ICE"
    assert all(not p.ean and not p.reference for p in repo.all())
    assert [p.drcloud_product_key for p in repo.all()] == identities


def test_manual_override_conflict_is_never_overwritten(tmp_path):
    p = product(710, variant_name="PÊCHE ICE", variant_source="MANUAL")
    repo = SQLiteOSRepository(tmp_path / "db.sqlite", [p])
    row = ProductObservation(p.drcloud_product_key, "PRESTASHOP", "710", variant_name="PEACH ICE")
    service = CatalogueRehydrationService(repo, backup=lambda: "backup")
    item = service.preview([row])["items"][0]
    assert item["classification"] == AMBIGUOUS
    service.apply_safe([row], actor="admin")
    assert repo.get(p.drcloud_product_key).variant_name == "PÊCHE ICE"


def test_duplicate_ean_is_ambiguous_and_backup_failure_prevents_apply(tmp_path):
    first, second = product(710, ean="4006381333931", ean_source="MANUAL"), product(711)
    repo = SQLiteOSRepository(tmp_path / "db.sqlite", [first, second])
    row = ProductObservation(second.drcloud_product_key, "PRESTASHOP", "711", ean="4006381333931")
    preview = CatalogueRehydrationService(repo).preview([row])
    target = next(x for x in preview["items"] if x["product_key"] == second.drcloud_product_key)
    assert target["fields"]["ean"] == AMBIGUOUS
    failing = CatalogueRehydrationService(repo, backup=lambda: (_ for _ in ()).throw(OSError("disk full")))
    before = [(p.drcloud_product_key, p.ean) for p in repo.all()]
    with pytest.raises(OSError): failing.apply_safe([row], actor="admin")
    assert [(p.drcloud_product_key, p.ean) for p in repo.all()] == before


def test_stock_count_is_unchanged(tmp_path):
    p = product(710); db = tmp_path / "db.sqlite"
    stock = SQLiteStockMovementRepository(db)
    stock.append(StockMovement(id="movement-1", drcloud_product_key=p.drcloud_product_key,
      quantity_delta=3, movement_type=MovementType.INVENTORY_CORRECTION, source_type="TEST",
      source_id="one", idempotency_key="one", status=MovementStatus.APPLIED))
    repo = SQLiteOSRepository(db, [p])
    service = CatalogueRehydrationService(repo, backup=lambda: "backup")
    row = ProductObservation(p.drcloud_product_key, "PRESTASHOP", "710", variant_name="PEACH ICE")
    before = len(stock.list()); service.apply_safe([row], actor="admin")
    assert len(stock.list()) == before and stock.current_quantity(p.drcloud_product_key) == 3


def test_repository_snapshot_contains_the_real_hyper_max_data():
    path = packaged_historical_snapshot()
    products = [product(710+i) for i in range(7)]
    assert [x.variant_name for x in historical_observations(path, products)] == NAMES


def test_packaged_snapshot_is_cwd_independent_and_has_478_unique_identities(tmp_path, monkeypatch):
    from dr_cloud_sync.rehydration import validate_historical_snapshot
    monkeypatch.chdir(tmp_path)
    document = validate_historical_snapshot(packaged_historical_snapshot())
    identities = {(str(parent["id"]), str(combination["id"]) if combination else None)
                  for parent in document["catalogue"]
                  for combination in (parent.get("declinaisons") or [None])}
    assert len(identities) == 478


def test_missing_or_corrupt_snapshot_fails_closed_without_mutation(tmp_path):
    from dr_cloud_sync.rehydration import HistoricalCatalogueUnavailable
    repo = SQLiteOSRepository(tmp_path / "db.sqlite", [product(710)])
    before = repo.all()
    for name, contents in (("missing.json", None), ("broken.json", "not json"),
                           ("duplicate.json", json.dumps({"catalogue": [
                               {"id": 100}, {"id": 100}]}))):
        path = tmp_path / name
        if contents is not None:
            path.write_text(contents)
        with pytest.raises(HistoricalCatalogueUnavailable, match="Analyse impossible"):
            historical_observations(path, repo.all())
        assert repo.all() == before
