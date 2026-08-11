import concurrent.futures
import json
import os
import sqlite3

import pytest

from dr_cloud_sync.backup_service import BackupService, BackupUnavailable


def database(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE product(id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO product(name) VALUES ('unchanged')")
    return path


def create(service, db, reason="TEST"):
    db.parent.joinpath("catalogue.json").write_text('[{"name":"test"}]')
    db.parent.joinpath("catalogue-report.json").write_text('{"ready_for_inventory":true}')
    return service.create(db, reason=reason, environment="test", safe_mode=True)


def test_production_like_backup_is_atomic_private_and_integral(tmp_path):
    root = tmp_path / "persistent" / "backups"
    result = create(BackupService(root), database(tmp_path / "source.db"))
    bundle = root / result["backup_id"]
    assert bundle.is_dir() and (bundle / "drcloud.db").is_file()
    assert result["status"] == "SUCCESS" and result["size_bytes"] > 0
    assert not list(root.glob("*.partial"))
    assert root.stat().st_mode & 0o077 == 0
    with sqlite3.connect(bundle / "drcloud.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_missing_directory_created_and_detected_after_restart(tmp_path):
    root = tmp_path / "new" / "backups"; db = database(tmp_path / "source.db")
    original = create(BackupService(root), db)
    detected = BackupService(root).successful()
    assert detected[0]["backup_id"] == original["backup_id"]


def test_permission_denied_is_clean_and_business_data_unchanged(tmp_path, monkeypatch):
    db = database(tmp_path / "source.db"); service = BackupService(tmp_path / "backups")
    monkeypatch.setattr(service, "health", lambda **_: {"available": False, "status": "error"})
    with pytest.raises(BackupUnavailable, match="stockage des sauvegardes"):
        create(service, db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT name FROM product").fetchone() == ("unchanged",)


def test_concurrent_backups_never_overwrite(tmp_path):
    service = BackupService(tmp_path / "backups"); db = database(tmp_path / "source.db")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(service, db), range(2)))
    assert len({row["backup_id"] for row in results}) == 2
    assert len(service.successful()) == 2


def test_partial_failure_never_publishes_success(tmp_path, monkeypatch):
    service = BackupService(tmp_path / "backups"); db = database(tmp_path / "source.db")
    monkeypatch.setattr(service, "_sqlite_check", lambda _: (_ for _ in ()).throw(sqlite3.DatabaseError("boom")))
    with pytest.raises(BackupUnavailable):
        create(service, db)
    assert service.successful() == []
    assert not list(service.root.glob("*.partial"))


def test_metadata_cannot_select_output_path(tmp_path):
    service = BackupService(tmp_path / "backups"); db = database(tmp_path / "source.db")
    result = create(service, db, reason="../../outside")
    assert not (tmp_path / "outside").exists()
    assert json.loads((service.root / result["backup_id"] / "metadata.json").read_text())["reason"] == "../../outside"


def test_bundle_contains_canonical_runtime_files_and_manifest_checksums(tmp_path):
    db=database(tmp_path/"source.db"); catalogue=tmp_path/"configured-catalogue.json"; report=tmp_path/"configured-report.json"
    catalogue.write_text('[{"name":"real"}]'); report.write_text('{"ready_for_inventory":true}')
    service=BackupService(tmp_path/"backups")
    result=service.create(db,reason="TEST",environment="test",safe_mode=True,catalogue=catalogue,mapping_report=report)
    bundle=service.root/result["backup_id"]; manifest=json.loads((bundle/"metadata.json").read_text())
    assert manifest["required_runtime_files"] == ["drcloud.db","catalogue.json","catalogue-report.json"]
    assert result["backup_class"] == "APP_RESTORABLE" and result["runtime_files_complete"] is True
    for item in manifest["files"]:
        path=bundle/item["path"]
        assert path.stat().st_size == item["size"]
        assert __import__("hashlib").sha256(path.read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("missing", ["catalogue.json", "catalogue-report.json"])
def test_verify_rejects_missing_required_runtime_file(tmp_path, missing):
    service=BackupService(tmp_path/"backups"); result=create(service,database(tmp_path/"source.db"))
    (service.root/result["backup_id"]/missing).unlink()
    with pytest.raises(BackupUnavailable): service.verify(service.root/result["backup_id"])


def test_verify_rejects_corrupted_runtime_checksum(tmp_path):
    service=BackupService(tmp_path/"backups"); result=create(service,database(tmp_path/"source.db")); bundle=service.root/result["backup_id"]
    (bundle/"catalogue.json").write_text("corrupt")
    with pytest.raises(BackupUnavailable): service.verify(bundle)


def test_create_fails_closed_when_runtime_state_is_missing(tmp_path):
    with pytest.raises(BackupUnavailable,match="BACKUP_INCOMPLETE_RUNTIME_STATE"):
        BackupService(tmp_path/"backups").create(database(tmp_path/"source.db"),reason="TEST",environment="production",safe_mode=True)
