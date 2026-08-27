import sqlite3

from dr_cloud_sync.sumup_merchant_sync_evidence import sumup_merchant_sync_evidence


def _db(tmp_path):
    path = tmp_path / "control-plane.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE data_sources(
              source_id TEXT PRIMARY KEY,status TEXT NOT NULL,enabled INTEGER NOT NULL,
              last_success_at TEXT,last_run_id INTEGER,records_available INTEGER,
              last_error TEXT,cursor TEXT);
            CREATE TABLE sync_jobs(
              job_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,job_type TEXT NOT NULL,
              status TEXT NOT NULL,attempts INTEGER NOT NULL,error TEXT);
            CREATE TABLE data_hub_sync_runs(
              run_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,
              status TEXT NOT NULL,result_json TEXT,error TEXT,started_at TEXT,completed_at TEXT);
            CREATE TABLE sumup_merchants(merchant_code TEXT PRIMARY KEY);
            """
        )
    return path


def _source(path, *, status="CONNECTED", enabled=1, last_success=None, last_run=None, records=None):
    with sqlite3.connect(path) as db:
        db.execute(
            """INSERT INTO data_sources
               (source_id,status,enabled,last_success_at,last_run_id,records_available,last_error,cursor)
               VALUES('sumup_merchant',?,?,?,?,?,?,?)""",
            (status, enabled, last_success, last_run, records, "SENSITIVE ERROR VALUE", "SENSITIVE CURSOR VALUE"),
        )


def _job(path, *, status="PENDING", attempts=0):
    with sqlite3.connect(path) as db:
        db.execute(
            """INSERT INTO sync_jobs(job_id,source_id,job_type,status,attempts,error)
               VALUES('sync_sumup_merchant','sumup_merchant','SUMUP_MERCHANT',?,?,?)""",
            (status, attempts, "SENSITIVE JOB ERROR"),
        )


def _run(path, status):
    with sqlite3.connect(path) as db:
        db.execute(
            """INSERT INTO data_hub_sync_runs
               (job_id,status,result_json,error,started_at,completed_at)
               VALUES('sync_sumup_merchant',?,?,?,?,?)""",
            (status, '{"merchant_code":"SECRET-MERCHANT"}', "SENSITIVE RUN ERROR",
             "2099-01-01T00:00:00Z", "2099-01-01T00:00:01Z"),
        )


def test_missing_database_is_fail_closed(tmp_path):
    result = sumup_merchant_sync_evidence(tmp_path / "missing.sqlite")
    assert result["evidence_status"] == "UNMEASURABLE"
    assert result["reason"] == "REQUIRED_LEDGER_MISSING"
    assert result["rpo_projection_authorized"] is False


def test_missing_control_plane_is_fail_closed(tmp_path):
    path = tmp_path / "incomplete.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sumup_merchants(merchant_code TEXT PRIMARY KEY)")
    result = sumup_merchant_sync_evidence(path)
    assert result["evidence_status"] == "UNMEASURABLE"
    assert result["reason"] == "REQUIRED_CONTROL_PLANE_MISSING"


def test_missing_required_column_is_fail_closed(tmp_path):
    path = _db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE data_sources RENAME TO old_data_sources")
        db.execute("CREATE TABLE data_sources(source_id TEXT PRIMARY KEY,status TEXT,enabled INTEGER)")
    result = sumup_merchant_sync_evidence(path)
    assert result["evidence_status"] == "UNMEASURABLE"
    assert result["reason"] == "REQUIRED_SCHEMA_INCOMPLETE"


def test_source_not_registered_is_bounded(tmp_path):
    path = _db(tmp_path)
    result = sumup_merchant_sync_evidence(path)
    assert result["evidence_status"] == "MEASURABLE"
    assert result["diagnosis"] == "SOURCE_NOT_REGISTERED"
    assert result["control_plane"]["merchant_rows_state"] == "ZERO"


def test_job_not_registered_is_bounded(tmp_path):
    path = _db(tmp_path)
    _source(path)
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "JOB_NOT_REGISTERED"


def test_registered_job_that_never_ran_is_distinguished(tmp_path):
    path = _db(tmp_path)
    _source(path, status="CONNECTED")
    _job(path, status="PENDING", attempts=0)
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "JOB_NEVER_RAN"
    assert result["control_plane"]["run_counts"]["total"] == 0
    assert result["control_plane"]["job_attempts_state"] == "ZERO"


def test_non_autorunnable_source_is_distinguished(tmp_path):
    path = _db(tmp_path)
    _source(path, status="ERROR")
    _job(path, status="FAILED", attempts=1)
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "SOURCE_NOT_AUTORUNNABLE"


def test_disabled_source_is_non_autorunnable_even_if_connected(tmp_path):
    path = _db(tmp_path)
    _source(path, status="CONNECTED", enabled=0)
    _job(path, status="PENDING", attempts=0)
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "SOURCE_NOT_AUTORUNNABLE"
    assert result["control_plane"]["source_enabled"] is False


def test_blocked_job_without_run_is_not_reported_as_never_ran(tmp_path):
    path = _db(tmp_path)
    _source(path, status="CONNECTED", enabled=1)
    _job(path, status="BLOCKED", attempts=0)
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "JOB_FAILED_OR_BLOCKED"
    assert result["control_plane"]["run_counts"]["total"] == 0
    assert result["control_plane"]["job_status"] == "BLOCKED"


def test_failed_run_is_distinguished_without_emitting_error(tmp_path):
    path = _db(tmp_path)
    _source(path, status="CONNECTED", last_run=1)
    _job(path, status="FAILED", attempts=1)
    _run(path, "FAILED")
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "JOB_FAILED_OR_BLOCKED"
    assert result["control_plane"]["latest_run_status"] == "FAILED"
    assert result["control_plane"]["run_counts"]["failed"] == 1
    text = repr(result)
    assert "SENSITIVE RUN ERROR" not in text
    assert "SENSITIVE JOB ERROR" not in text
    assert "SENSITIVE ERROR VALUE" not in text
    assert "SENSITIVE CURSOR VALUE" not in text
    assert "SECRET-MERCHANT" not in text
    assert "2099-01-01" not in text


def test_succeeded_run_with_empty_ledger_is_explicit(tmp_path):
    path = _db(tmp_path)
    _source(path, status="CONNECTED", last_success="2099-01-01T00:00:01Z", last_run=1, records=0)
    _job(path, status="SUCCEEDED", attempts=1)
    _run(path, "SUCCEEDED")
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "JOB_SUCCEEDED_NO_LEDGER_ROWS"
    assert result["control_plane"]["source_last_success_presence"] == "PRESENT"
    assert result["control_plane"]["source_records_available_state"] == "ZERO"
    assert result["control_plane"]["merchant_rows_state"] == "ZERO"
    assert result["provider_exhaustiveness_inferred"] is False
    assert result["rpo_projection_authorized"] is False
    assert result["safety"]["provider_network_calls"] is False
    assert result["safety"]["imported_at_used_as_business_progress"] is False
    assert "2099-01-01" not in repr(result)


def test_existing_merchant_row_wins_over_control_plane_state(tmp_path):
    path = _db(tmp_path)
    _source(path, status="CONNECTED", records=1)
    _job(path, status="SUCCEEDED", attempts=1)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sumup_merchants(merchant_code) VALUES('SECRET-MERCHANT')")
    result = sumup_merchant_sync_evidence(path)
    assert result["diagnosis"] == "LEDGER_HAS_DATA"
    assert result["control_plane"]["merchant_rows_state"] == "NONZERO"
    assert "SECRET-MERCHANT" not in repr(result)
