"""Persistent, resumable jobs for synchronous DrCloud operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping
from uuid import uuid4


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"


@dataclass(frozen=True)
class JobRun:
    job_id: str
    job_type: str
    connector: str
    operation: str
    status: JobStatus = JobStatus.PENDING
    attempt: int = 0
    max_attempts: int = 3
    idempotency_key: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    started_at: str | None = None
    completed_at: str | None = None
    next_retry_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    summary: Mapping[str, Any] = field(default_factory=dict)


SYNC_RUN_COLUMNS = {
    "job_id": "TEXT",
    "job_type": "TEXT NOT NULL DEFAULT 'PRESTASHOP_SNAPSHOT'",
    "connector": "TEXT NOT NULL DEFAULT 'PRESTASHOP'",
    "operation": "TEXT NOT NULL DEFAULT 'SNAPSHOT_PULL'",
    "attempt": "INTEGER NOT NULL DEFAULT 1",
    "max_attempts": "INTEGER NOT NULL DEFAULT 3",
    "idempotency_key": "TEXT",
    "created_at": "TEXT",
    "next_retry_at": "TEXT",
    "error_code": "TEXT",
}


class SqliteJobRepository:
    """SQLite adapter; conditional updates provide the single-job execution lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
                status TEXT NOT NULL, counts_json TEXT, error TEXT)""")
            present = {row[1] for row in db.execute("PRAGMA table_info(sync_runs)")}
            for name, definition in SYNC_RUN_COLUMNS.items():
                if name not in present:
                    db.execute(f"ALTER TABLE sync_runs ADD COLUMN {name} {definition}")
            # Legacy numeric identity is retained and deterministically exposed as a stable job identity.
            db.execute("""UPDATE sync_runs SET job_id='legacy-sync-' || id
                          WHERE job_id IS NULL OR job_id=''""")
            db.execute("UPDATE sync_runs SET created_at=started_at WHERE created_at IS NULL")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_sync_runs_job_id ON sync_runs(job_id)")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_sync_runs_idempotency
                          ON sync_runs(job_type, idempotency_key)
                          WHERE idempotency_key IS NOT NULL""")

    def create(self, *, job_type: str, connector: str, operation: str,
               job_id: str | None = None, idempotency_key: str | None = None,
               max_attempts: int = 3) -> JobRun:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if idempotency_key:
            existing = self.get_by_idempotency_key(job_type, idempotency_key)
            if existing:
                return existing
        identifier, now = job_id or str(uuid4()), _now()
        try:
            with self._connect() as db:
                db.execute("""INSERT INTO sync_runs
                    (job_id,job_type,connector,operation,status,attempt,max_attempts,
                     idempotency_key,created_at,started_at)
                    VALUES (?,?,?,?,?,0,?,?,?,?)""",
                    (identifier, job_type, connector, operation, JobStatus.PENDING.value,
                     max_attempts, idempotency_key, now, now))
        except sqlite3.IntegrityError:
            existing = (self.get(identifier) or
                        (self.get_by_idempotency_key(job_type, idempotency_key) if idempotency_key else None))
            if existing:
                return existing
            raise
        return self.get(identifier)  # type: ignore[return-value]

    def get(self, job_id: str) -> JobRun | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sync_runs WHERE job_id=?", (job_id,)).fetchone()
        return _job(row) if row else None

    def get_by_idempotency_key(self, job_type: str, key: str) -> JobRun | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sync_runs WHERE job_type=? AND idempotency_key=?",
                             (job_type, key)).fetchone()
        return _job(row) if row else None

    def mark_running(self, job_id: str) -> JobRun | None:
        """Acquire a job exactly once; attempts count actual successful acquisitions."""
        with self._connect() as db:
            cursor = db.execute("""UPDATE sync_runs SET status=?, attempt=attempt+1,
                started_at=?, completed_at=NULL, next_retry_at=NULL,
                error_code=NULL, error=NULL
                WHERE job_id=? AND status IN (?,?,?) AND attempt < max_attempts""",
                (JobStatus.RUNNING.value, _now(), job_id, JobStatus.PENDING.value,
                 JobStatus.FAILED.value, JobStatus.RETRYABLE.value))
            if cursor.rowcount != 1:
                return None
        return self.get(job_id)

    def mark_succeeded(self, job_id: str, summary: Mapping[str, Any]) -> JobRun:
        encoded = json.dumps(dict(summary), ensure_ascii=False, sort_keys=True)
        if len(encoded) > 10_000:
            raise ValueError("job summary is too large")
        return self._finish(job_id, JobStatus.SUCCEEDED, counts_json=encoded)

    def mark_failed(self, job_id: str, error: BaseException, *, retryable: bool = False,
                    next_retry_at: str | None = None) -> JobRun:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        status = JobStatus.RETRYABLE if retryable and current.attempt < current.max_attempts else JobStatus.FAILED
        return self._finish(job_id, status, error_code=type(error).__name__[:100],
                            error=sanitize_error(error), next_retry_at=next_retry_at)

    def _finish(self, job_id: str, status: JobStatus, **values: Any) -> JobRun:
        assignments = ["status=?", "completed_at=?"] + [f"{key}=?" for key in values]
        params = [status.value, _now(), *values.values(), job_id, JobStatus.RUNNING.value]
        with self._connect() as db:
            cursor = db.execute(f"UPDATE sync_runs SET {','.join(assignments)} WHERE job_id=? AND status=?", params)
            if cursor.rowcount != 1:
                raise ValueError(f"invalid job transition to {status.value}")
        return self.get(job_id)  # type: ignore[return-value]

    def list_recent(self, limit: int = 20) -> list[JobRun]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_job(row) for row in rows]

    def find_recoverable(self) -> list[JobRun]:
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM sync_runs WHERE status IN (?,?)
                AND attempt < max_attempts ORDER BY id""",
                (JobStatus.FAILED.value, JobStatus.RETRYABLE.value)).fetchall()
        return [_job(row) for row in rows]


class JobRunner:
    def __init__(self, repository: SqliteJobRepository) -> None:
        self.repository = repository

    def run(self, job: JobRun, operation: Callable[[], Mapping[str, Any]], *,
            retryable: Callable[[BaseException], bool] | None = None) -> JobRun:
        current = self.repository.get(job.job_id)
        if current is None:
            raise KeyError(job.job_id)
        if current.status == JobStatus.SUCCEEDED:
            return current
        acquired = self.repository.mark_running(job.job_id)
        if acquired is None:
            raise RuntimeError("job is already running or cannot be retried")
        try:
            return self.repository.mark_succeeded(job.job_id, operation())
        except Exception as exc:
            self.repository.mark_failed(job.job_id, exc, retryable=bool(retryable and retryable(exc)))
            raise


def sanitize_error(error: BaseException) -> str:
    message = str(error) if getattr(error, "operator_safe", False) else f"{type(error).__name__}: {error}"
    message = re.sub(r"(?i)(api[_-]?key|token|password|authorization)(\s*[:=]\s*)[^\s,;]+", r"\1\2[REDACTED]", message)
    message = re.sub(r"(?i)(https?://[^:/\s]+:)[^@/\s]+@", r"\1[REDACTED]@", message)
    return message[:500]


def _job(row: sqlite3.Row) -> JobRun:
    legacy = {"running": JobStatus.RUNNING, "completed": JobStatus.SUCCEEDED, "failed": JobStatus.FAILED}
    status = legacy[row["status"]] if row["status"] in legacy else JobStatus(row["status"])
    return JobRun(job_id=row["job_id"], job_type=row["job_type"], connector=row["connector"],
                  operation=row["operation"], status=status, attempt=row["attempt"],
                  max_attempts=row["max_attempts"], idempotency_key=row["idempotency_key"],
                  created_at=row["created_at"], started_at=row["started_at"],
                  completed_at=row["completed_at"], next_retry_at=row["next_retry_at"],
                  error_code=row["error_code"], error_message=row["error"],
                  summary=json.loads(row["counts_json"] or "{}"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
