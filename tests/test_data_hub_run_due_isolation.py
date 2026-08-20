from datetime import datetime, timezone

from dr_cloud_sync.data_hub import DataHub, JobDefinition


def test_run_due_contains_one_job_failure_and_continues_unrelated_jobs(tmp_path):
    now = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)
    hub = DataHub(tmp_path / "hub.sqlite", clock=lambda: now)
    hub.register_source("optional", "OPTIONAL", "Provider", configured=True)
    hub.register_source("core", "CORE", "Provider", configured=True)
    hub.register_job(JobDefinition("a_optional", "optional", "OPTIONAL", 60))
    hub.register_job(JobDefinition("z_core", "core", "CORE", 60))

    seen = []

    def fail(_cursor):
        raise RuntimeError("optional scope denied")

    def succeed(_cursor):
        seen.append("core")
        return {"rows_imported": 1, "records_available": 1}

    results = hub.run_due({"OPTIONAL": fail, "CORE": succeed})

    assert seen == ["core"]
    assert len(results) == 2
    assert hub.job("a_optional")["status"] == "FAILED"
    assert hub.job("z_core")["status"] == "SUCCEEDED"
    sources = {source["source_id"]: source for source in hub.sources()}
    assert sources["optional"]["status"] == "ERROR"
    assert sources["core"]["status"] == "CONNECTED"
