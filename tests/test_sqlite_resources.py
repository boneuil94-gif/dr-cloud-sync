import os

import pytest

from dr_cloud_sync.data_hub import DataHub, JobDefinition
from dr_cloud_sync.jobs import SqliteJobRepository


def fd_count() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except FileNotFoundError:
        pytest.skip("descriptor accounting requires Linux /proc")


def test_short_lived_sqlite_operations_release_descriptors(tmp_path):
    database = tmp_path / "resources.db"
    baseline = fd_count()

    for index in range(50):
        jobs = SqliteJobRepository(database)
        jobs.create(job_type="TEST", connector="LOCAL", operation="CHECK",
                    job_id=f"run-{index}")
        jobs.list_recent(5)

        hub = DataHub(database)
        hub.register_source(f"source-{index}", "TEST", "LOCAL", configured=True)
        hub.register_job(JobDefinition(f"job-{index}", f"source-{index}", "TEST", 60))
        hub.sources()
        hub.jobs()

    # Account for unrelated descriptors pytest/plugins may transiently retain,
    # while detecting the hundreds of SQLite handles leaked by the old code.
    assert fd_count() <= baseline + 3
