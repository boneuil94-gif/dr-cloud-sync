import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from dr_cloud_sync.domain import MovementStatus, MovementType, StockMovement
from dr_cloud_sync.inventory import InventoryRepository
from dr_cloud_sync.repositories import DuplicateStockMovement, SQLiteStockMovementRepository
from dr_cloud_sync.services import StockMovementConflict, StockService


def movement(**changes):
    base = StockMovement(
        id="movement-1",
        drcloud_product_key="drc:p:42",
        quantity_delta=3,
        movement_type=MovementType.INVENTORY_CORRECTION,
        source_type="INVENTORY",
        source_id="inventory-2026-07",
        idempotency_key="inventory-2026-07:drc:p:42",
        actor="operator-1",
    )
    return replace(base, **changes)


def test_create_read_persist_and_reconnect(tmp_path):
    path = tmp_path / "ledger.sqlite"
    repository = SQLiteStockMovementRepository(path)
    result = StockService(repository).record(movement())

    assert result.created is True
    assert result.movement.status is MovementStatus.PENDING
    assert repository.get("movement-1") == result.movement
    assert repository.by_idempotency_key("INVENTORY", movement().idempotency_key) == result.movement

    reopened = SQLiteStockMovementRepository(path)
    assert reopened.get("movement-1") == result.movement
    columns = {row[1] for row in reopened.db.execute("PRAGMA table_info(stock_movements)")}
    assert {"drcloud_product_key", "source_type", "idempotency_key", "status", "applied_at"} <= columns


def test_exact_replay_returns_existing_without_second_movement(tmp_path):
    repository = SQLiteStockMovementRepository(tmp_path / "ledger.sqlite")
    service = StockService(repository)
    first = service.record(movement())
    replay = service.record(replace(movement(), id="another-id"))

    assert first.created is True
    assert replay.created is False
    assert replay.movement.id == "movement-1"
    assert len(repository.list()) == 1


def test_same_scoped_key_with_different_payload_is_rejected(tmp_path):
    repository = SQLiteStockMovementRepository(tmp_path / "ledger.sqlite")
    service = StockService(repository)
    service.record(movement())

    with pytest.raises(StockMovementConflict, match="different stock movement payload"):
        service.record(replace(movement(), id="movement-2", quantity_delta=4))
    assert len(repository.list()) == 1


def test_database_unique_constraint_is_scoped_by_source_type(tmp_path):
    repository = SQLiteStockMovementRepository(tmp_path / "ledger.sqlite")
    repository.append(movement())
    with pytest.raises(DuplicateStockMovement):
        repository.append(replace(movement(), id="movement-2"))
    # An independently owned key with the same text is valid in another source.
    repository.append(replace(movement(), id="movement-3", source_type="SALE"))
    assert len(repository.list()) == 2


def test_failed_insert_rolls_back_and_status_validation_is_strict(tmp_path):
    repository = SQLiteStockMovementRepository(tmp_path / "ledger.sqlite")
    repository.append(movement())
    with pytest.raises(DuplicateStockMovement):
        repository.append(replace(movement(), id="movement-2"))
    assert repository.get("movement-2") is None

    with pytest.raises(ValueError, match="pending and unapplied"):
        StockService(repository).record(
            replace(movement(), id="movement-3", idempotency_key="other", status=MovementStatus.APPLIED))


@pytest.mark.parametrize("movement_type", list(MovementType))
def test_all_documented_movement_types_are_persistable(tmp_path, movement_type):
    repository = SQLiteStockMovementRepository(tmp_path / f"{movement_type}.sqlite")
    item = replace(movement(), movement_type=movement_type)
    repository.append(item)
    assert repository.get(item.id).movement_type is movement_type


def test_model_rejects_missing_identity_and_zero_or_boolean_delta():
    with pytest.raises(ValueError, match="idempotency_key"):
        movement(idempotency_key="")
    for delta in (0, True):
        with pytest.raises(ValueError, match="non-zero integer"):
            movement(quantity_delta=delta)


def test_additive_migration_preserves_legacy_rows(tmp_path):
    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE stock_movements(
      id TEXT PRIMARY KEY, prestashop_key TEXT NOT NULL, quantity_delta INTEGER NOT NULL,
      movement_type TEXT NOT NULL, source_id TEXT NOT NULL, created_at TEXT NOT NULL,
      validated_at TEXT)""")
    db.execute("INSERT INTO stock_movements VALUES(?,?,?,?,?,?,?)", (
        "legacy-1", "p:42", 2, "INVENTORY_CORRECTION", "inventory-old",
        "2025-01-01T00:00:00+00:00", None))
    db.commit()
    db.close()

    repository = SQLiteStockMovementRepository(path)
    row = repository.db.execute("SELECT * FROM stock_movements WHERE id='legacy-1'").fetchone()
    assert row["prestashop_key"] == "p:42"
    assert row["drcloud_product_key"] == "drc:p:42"
    assert row["source_type"] == "LEGACY"
    assert row["idempotency_key"] == "legacy:legacy-1"
    assert repository.get("legacy-1").drcloud_product_key == "drc:p:42"
    # Re-running both startup paths is idempotent.
    InventoryRepository(path)
    SQLiteStockMovementRepository(path)
    assert repository.db.execute("SELECT count(*) FROM stock_movements").fetchone()[0] == 1


def test_concurrent_replay_creates_one_row(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    SQLiteStockMovementRepository(path)

    def submit(identifier):
        repository = SQLiteStockMovementRepository(path)
        return StockService(repository).record(replace(movement(), id=identifier))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("concurrent-1", "concurrent-2")))

    repository = SQLiteStockMovementRepository(path)
    assert sorted(result.created for result in results) == [False, True]
    assert len(repository.list()) == 1
    assert results[0].movement.id == results[1].movement.id
