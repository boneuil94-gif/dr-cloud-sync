from __future__ import annotations

import multiprocessing
import sqlite3
import time
from pathlib import Path

import pytest

from dr_cloud_sync.data_hub import BatchAlreadyRunning, DataHub, JobDefinition


def _heartbeat_worker(database: str, worker_id: str, iterations: int) -> None:
    hub = DataHub(Path(database))
    for _ in range(iterations):
        hub.heartbeat(worker_id)


def _held_batch_worker(database: str, ready, release) -> None:
    hub = DataHub(Path(database))

    def operation(_cursor):
        ready.set()
        if not release.wait(10):
            raise RuntimeError("release timeout")
        return {"rows_imported": 1}

    result = hub.run_all({"TEST_MULTIWORKER": operation}, triggered_by="worker-a")
    if result["status"] != "SUCCEEDED":
        raise RuntimeError(f"unexpected batch status: {result['status']}")


def _crash_with_open_write(database: str, ready) -> None:
    db = sqlite3.connect(database, timeout=10)
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO crash_probe(value) VALUES('uncommitted')")
    ready.set()
    while True:
        time.sleep(1)


def _spawn_context():
    # Production and GitHub runners are process-based Linux environments. Using
    # spawn avoids inheriting SQLite handles from pytest and proves independent
    # processes can safely reopen the same durable database.
    return multiprocessing.get_context("spawn")


def test_independent_workers_can_write_heartbeats_to_one_database(tmp_path):
    database = tmp_path / "drcloud.db"
    DataHub(database)  # schema is prepared before workers race on normal writes
    ctx = _spawn_context()
    processes = [
        ctx.Process(target=_heartbeat_worker, args=(str(database), f"worker-{index}", 25))
        for index in range(4)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0

    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        rows = db.execute(
            "SELECT worker_id, database_fingerprint FROM automation_worker_heartbeat ORDER BY worker_id"
        ).fetchall()

    assert [row[0] for row in rows] == [f"worker-{index}" for index in range(4)]
    assert len({row[1] for row in rows}) == 1


def test_global_sync_batch_lease_is_enforced_across_processes(tmp_path):
    database = tmp_path / "drcloud.db"
    hub = DataHub(database)
    hub.register_source("multiworker", "TEST", "LOCAL", configured=True)
    hub.register_job(JobDefinition("multiworker-job", "multiworker", "TEST_MULTIWORKER", 60))

    ctx = _spawn_context()
    ready = ctx.Event()
    release = ctx.Event()
    process = ctx.Process(target=_held_batch_worker, args=(str(database), ready, release))
    process.start()
    try:
        assert ready.wait(10), "first worker never entered the running batch"
        competing = DataHub(database)
        with pytest.raises(BatchAlreadyRunning, match="BATCH_ALREADY_RUNNING"):
            competing.run_all(
                {"TEST_MULTIWORKER": lambda _cursor: {"rows_imported": 1}},
                triggered_by="worker-b",
            )
    finally:
        release.set()
        process.join(20)

    assert process.exitcode == 0
    result = DataHub(database).latest_batch()
    assert result is not None
    assert result["status"] == "SUCCEEDED"
    assert result["summary"] == {
        "jobs_total": 1,
        "jobs_succeeded": 1,
        "jobs_failed": 0,
        "jobs_blocked": 0,
        "jobs_skipped": 0,
    }


def test_killed_writer_leaves_database_integrity_and_future_writes_intact(tmp_path):
    database = tmp_path / "drcloud.db"
    DataHub(database)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE crash_probe(value TEXT NOT NULL)")

    ctx = _spawn_context()
    ready = ctx.Event()
    process = ctx.Process(target=_crash_with_open_write, args=(str(database), ready))
    process.start()
    assert ready.wait(10), "crash worker never acquired its write transaction"
    process.terminate()
    process.join(10)
    assert process.exitcode is not None

    with sqlite3.connect(database, timeout=10) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        # The killed transaction must roll back completely, then a new writer must
        # be able to commit without manual repair.
        assert db.execute("SELECT count(*) FROM crash_probe").fetchone()[0] == 0
        db.execute("INSERT INTO crash_probe(value) VALUES('committed-after-crash')")
        db.commit()
        assert db.execute("SELECT count(*) FROM crash_probe").fetchone()[0] == 1
