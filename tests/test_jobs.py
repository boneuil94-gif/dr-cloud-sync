from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dr_cloud_sync.jobs import JobRunner, JobStatus, SqliteJobRepository


def repository(tmp_path: Path) -> SqliteJobRepository:
    return SqliteJobRepository(tmp_path / "jobs.sqlite3")


def test_job_lifecycle_stable_identity_and_persistence(tmp_path: Path):
    repo = repository(tmp_path)
    job = repo.create(job_type="TEST", connector="LOCAL", operation="COUNT", job_id="stable-1")
    assert job.job_id == "stable-1"
    assert job.status == JobStatus.PENDING and job.attempt == 0

    result = JobRunner(repo).run(job, lambda: {"items": 4})
    assert result.status == JobStatus.SUCCEEDED
    assert result.attempt == 1 and result.summary == {"items": 4}
    assert SqliteJobRepository(repo.path).get("stable-1") == result


def test_idempotency_and_success_are_not_reexecuted(tmp_path: Path):
    repo = repository(tmp_path)
    first = repo.create(job_type="SYNC", connector="X", operation="PULL",
                        idempotency_key="event-7")
    replay = repo.create(job_type="SYNC", connector="X", operation="PULL",
                         idempotency_key="event-7")
    assert replay.job_id == first.job_id
    calls = []
    runner = JobRunner(repo)
    runner.run(first, lambda: calls.append(1) or {"items": 1})
    runner.run(first, lambda: calls.append(2) or {"items": 2})
    assert calls == [1]


def test_failed_job_can_resume_and_attempts_are_bounded(tmp_path: Path):
    repo = repository(tmp_path)
    job = repo.create(job_type="TEST", connector="LOCAL", operation="FAIL", max_attempts=2)
    with pytest.raises(RuntimeError, match="temporary"):
        JobRunner(repo).run(job, lambda: (_ for _ in ()).throw(RuntimeError("temporary")),
                            retryable=lambda _: True)
    failed = repo.get(job.job_id)
    assert failed and failed.status == JobStatus.RETRYABLE and failed.attempt == 1
    result = JobRunner(repo).run(failed, lambda: {"recovered": 1})
    assert result.job_id == job.job_id and result.attempt == 2
    assert result.status == JobStatus.SUCCEEDED


def test_atomic_acquisition_prevents_double_execution(tmp_path: Path):
    repo = repository(tmp_path)
    job = repo.create(job_type="TEST", connector="LOCAL", operation="LOCK")
    acquired = repo.mark_running(job.job_id)
    assert acquired and acquired.attempt == 1
    assert SqliteJobRepository(repo.path).mark_running(job.job_id) is None


def test_error_is_bounded_and_secrets_are_redacted(tmp_path: Path):
    repo = repository(tmp_path)
    job = repo.create(job_type="TEST", connector="LOCAL", operation="FAIL")
    error = RuntimeError("api_key=very-secret password:also-secret " + "x" * 700)
    with pytest.raises(RuntimeError):
        JobRunner(repo).run(job, lambda: (_ for _ in ()).throw(error))
    failed = repo.get(job.job_id)
    assert failed and len(failed.error_message or "") <= 500
    assert "very-secret" not in (failed.error_message or "")
    assert "also-secret" not in (failed.error_message or "")


def test_additive_migration_keeps_legacy_sync_run_readable(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
            status TEXT NOT NULL, counts_json TEXT, error TEXT)""")
        db.execute("INSERT INTO sync_runs VALUES (1,'old-start','old-end','completed','{\"products\": 2}',NULL)")
    repo = SqliteJobRepository(path)
    legacy = repo.get("legacy-sync-1")
    assert legacy and legacy.status == JobStatus.SUCCEEDED
    assert legacy.summary == {"products": 2} and legacy.created_at == "old-start"
