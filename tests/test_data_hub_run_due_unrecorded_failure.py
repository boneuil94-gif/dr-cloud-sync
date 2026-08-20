from datetime import datetime, timezone
import sqlite3

import pytest

from dr_cloud_sync.data_hub import DataHub, JobDefinition


def test_run_due_reraises_failure_not_recorded_by_run(tmp_path, monkeypatch):
    now = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)
    hub = DataHub(tmp_path / "hub.sqlite", clock=lambda: now)
    hub.register_source("source", "SOURCE", "Provider", configured=True)
    hub.register_job(JobDefinition("job", "source", "SOURCE", 60))

    def fail_before_recording(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(hub, "run", fail_before_recording)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        hub.run_due({"SOURCE": lambda _cursor: {"rows_imported": 0}})

    assert hub.job("job")["status"] == "PENDING"
    assert {source["source_id"]: source for source in hub.sources()}["source"]["status"] == "CONNECTED"
