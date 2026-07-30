"""Catalogue ingestion orchestration. There is intentionally no Shop Caisse adapter."""

from __future__ import annotations

from typing import Any

from .prestashop import PrestaShopClient
from .jobs import JobRun, JobRunner, SqliteJobRepository
from .store import SnapshotStore


def synchronize(client: PrestaShopClient, store: SnapshotStore, *, job_id: str | None = None,
                idempotency_key: str | None = None, max_attempts: int = 3) -> dict[str, int]:
    """Fetch every master resource before atomically replacing the local snapshot."""
    repository = SqliteJobRepository(store.path)
    job = repository.create(job_type="CATALOG_SNAPSHOT", connector="PRESTASHOP",
                            operation="SNAPSHOT_PULL", job_id=job_id,
                            idempotency_key=idempotency_key, max_attempts=max_attempts)

    def pull_and_replace() -> dict[str, int]:
        # All remote pages are materialised before the local transaction starts.
        resources: dict[str, list[dict[str, Any]]] = {
            resource: list(client.iter_resource(resource)) for resource in client.RESOURCES
        }
        with store.connect() as connection:
            return store.replace_snapshot(connection, None, resources, job_id=job.job_id)

    result: JobRun = JobRunner(repository).run(job, pull_and_replace,
                                                 retryable=lambda _: True)
    return {str(key): int(value) for key, value in result.summary.items()}
