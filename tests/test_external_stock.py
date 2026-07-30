from datetime import datetime, timezone
import json

import pytest

from dr_cloud_sync.domain import MovementStatus, MovementType, Product, StockMovement
from dr_cloud_sync.external_stock import ExternalStockQueryService
from dr_cloud_sync.jobs import SqliteJobRepository
from dr_cloud_sync.repositories import SQLiteStockMovementRepository
from dr_cloud_sync.store import SnapshotStore


def product():
    return Product("drc:p:1", "p:1", 1, 0, "sc-1", "Produit", reference="REF")


def snapshot(path, job_id, quantity, observed=None):
    jobs=SqliteJobRepository(path)
    job=jobs.create(job_type="CATALOG_SNAPSHOT",connector="PRESTASHOP",operation="SNAPSHOT_PULL",job_id=job_id)
    jobs.mark_running(job.job_id)
    resources={"stock_availables":[{"id":1,"id_product":1,"id_product_attribute":0,"quantity":quantity}]}
    with SnapshotStore(path).connect() as db:
        SnapshotStore(path).replace_snapshot(db,None,resources,job_id=job_id)
        if observed:
            db.execute("UPDATE external_stock_observations SET observed_at=? WHERE job_id=?",(observed,job_id)); db.commit()
    jobs.mark_succeeded(job_id,{"stock_availables":1})


def test_valid_observation_equality_difference_freshness_and_no_ledger_write(tmp_path):
    path=tmp_path/"db.sqlite"; ledger=SQLiteStockMovementRepository(path)
    movement=StockMovement("drc:p:1",4,MovementType.INVENTORY_CORRECTION,"INVENTORY","s","k",MovementStatus.APPLIED,validated_at="2026-07-30T00:00:00+00:00",applied_at="2026-07-30T00:00:00+00:00")
    ledger.append(movement); snapshot(path,"good",4,"2026-07-30T10:00:00+00:00")
    service=ExternalStockQueryService(path,[product()],ledger)
    assert service.comparisons(datetime(2026,7,30,11,tzinfo=timezone.utc))[0]["status"]=="MATCH"
    assert service.comparisons(datetime(2026,8,1,11,tzinfo=timezone.utc))[0]["status"]=="STALE"
    assert len(ledger.list())==1


def test_failed_partial_snapshot_does_not_replace_previous_valid_observation(tmp_path):
    path=tmp_path/"db.sqlite"; ledger=SQLiteStockMovementRepository(path); snapshot(path,"good",7)
    jobs=SqliteJobRepository(path); failed=jobs.create(job_type="CATALOG_SNAPSHOT",connector="PRESTASHOP",operation="SNAPSHOT_PULL",job_id="failed")
    jobs.mark_running(failed.job_id)
    with pytest.raises(ValueError):
        with SnapshotStore(path).connect() as db:
            SnapshotStore(path).replace_snapshot(db,None,{"products":[{"id":1}]},job_id="failed")
    jobs.mark_failed("failed",RuntimeError("token=secret"))
    row=ExternalStockQueryService(path,[product()],ledger).comparisons()[0]
    assert row["prestashop"]["job_id"]=="good" and row["prestashop"]["quantity"]==7
    assert "secret" not in (jobs.get("failed").error_message or "")


def test_absent_and_ambiguous_mapping_are_not_forced(tmp_path):
    path=tmp_path/"db.sqlite"; ledger=SQLiteStockMovementRepository(path)
    service=ExternalStockQueryService(path,[product()],ledger)
    assert service.comparisons()[0]["status"]=="UNKNOWN"
    snapshot(path,"good",2)
    duplicate=Product("drc:p:duplicate","p:duplicate",1,0,"sc-2","Duplicate")
    rows=ExternalStockQueryService(path,[product(),duplicate],ledger).comparisons()
    assert {row["status"] for row in rows}=={"INCONSISTENT"}
