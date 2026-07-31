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
