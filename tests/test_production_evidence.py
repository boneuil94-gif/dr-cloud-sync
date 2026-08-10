import json
import sqlite3
from datetime import datetime, timedelta, timezone

from dr_cloud_sync.production_evidence import (backup_inventory, coverage_contract,
    reconciliation_report, restore_test, rollback_check, sanitize, sha_status, snapshot)


def make_backup(tmp_path, *, corrupt=False, age=0):
    root=tmp_path/"backups"; bundle=root/"one"; bundle.mkdir(parents=True)
    db=bundle/"drcloud.db"
    if corrupt: db.write_bytes(b"not sqlite")
    else:
        with sqlite3.connect(db) as conn:
            conn.executescript("CREATE TABLE sales(id INTEGER PRIMARY KEY); CREATE TABLE sale_payments(id INTEGER); INSERT INTO sales VALUES(1); INSERT INTO sale_payments VALUES(1);")
    stamp=datetime.now(timezone.utc)-timedelta(seconds=age)
    (bundle/"metadata.json").write_text(json.dumps({"created_at":stamp.isoformat()}))
    return root


def test_sha_match_mismatch_and_unknown():
    assert sha_status("a","a","a")=="MATCH"
    assert sha_status("a","b","a")=="MISMATCH"
    assert sha_status("a",None,"a")=="UNKNOWN"


def test_coverage_unknown_partial_and_complete_are_not_freshness():
    base={"source_id":"sales","provider":"ShopCaisse","freshness":"FRESH","configuration":"CONFIGURED","rows_imported":5}
    unknown=coverage_contract(base); assert unknown["coverage_ratio"] is None and unknown["evidence_status"]=="FRESH_UNKNOWN_COVERAGE"
    partial=coverage_contract({**base,"provider_total":10}); assert partial["coverage_ratio"]==.5 and partial["evidence_status"]=="FRESH_PARTIAL"
    complete=coverage_contract({**base,"provider_total":5}); assert complete["evidence_status"]=="FRESH_COMPLETE"


def test_backup_inventory_stale_and_restore_success(tmp_path):
    root=make_backup(tmp_path,age=90000)
    assert backup_inventory(root)["status"]=="BACKUP_STALE"
    report=restore_test(root)
    assert report["restore_result"]=="RESTORE_PROVEN" and report["integrity_check"]=="ok"
    assert report["observed_rpo"] is not None and report["observed_rto"] is not None


def test_restore_missing_corrupt_and_rollback_not_proven(tmp_path):
    assert restore_test(tmp_path/"missing")["restore_result"]=="RESTORE_NOT_PROVEN"
    assert restore_test(make_backup(tmp_path/"bad",corrupt=True))["restore_result"]=="RESTORE_FAILED"
    assert rollback_check()["result"]=="ROLLBACK_NOT_PROVEN"


def test_reconciliation_partial_and_old_sqlite(tmp_path):
    db=make_backup(tmp_path).joinpath("one/drcloud.db")
    report=reconciliation_report(db)
    assert report["sales_total"]==1 and report["payments_total"]==1
    assert report["reconciliation_coverage"] is None and report["evidence_status"]=="TESTED"


def test_snapshot_has_no_secret_or_pii(tmp_path):
    db=make_backup(tmp_path).joinpath("one/drcloud.db")
    value=snapshot(database=db,environment="test",expected_commit=None,deployed_commit=None,public_url=None,
                   sources=[{"source_id":"x","provider":"x","freshness":"FRESH","api_key":"leak","rows_imported":1}])
    text=json.dumps(value).lower()
    assert "api_key" not in text and "leak" not in text and value["production_evidence_snapshot"]["sha_status"]=="UNKNOWN"


def test_recursive_sanitizer():
    assert sanitize({"token":"bad","safe":{"password":"bad","count":2},"message":"Bearer abc"})=={"safe":{"count":2},"message":"[REDACTED]"}
