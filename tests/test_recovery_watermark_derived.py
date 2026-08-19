import sqlite3

from dr_cloud_sync.recovery_watermark import (
    DERIVED_SOURCE_PARENTS,
    capture_recovery_watermark,
    compare_recovery_watermarks,
)


SCHEMA = """CREATE TABLE data_sources(
 source_id TEXT PRIMARY KEY, source_type TEXT, provider TEXT, status TEXT,
 enabled INTEGER, last_success_at TEXT, stale_after_seconds INTEGER,
 data_min_at TEXT, data_max_at TEXT, records_available INTEGER)"""


def _insert(db, source_id, *, maximum=None, records=None):
    db.execute(
        "INSERT INTO data_sources VALUES(?,?,?,?,?,?,?,?,?,?)",
        (source_id, source_id.upper(), "SumUp", "CONNECTED", 1,
         "2026-08-19T12:00:00Z", 3600, None, maximum, records),
    )


def test_transaction_detail_projections_are_not_independent_rpo_sources(tmp_path):
    path = tmp_path / "drcloud.db"
    with sqlite3.connect(path) as db:
        db.execute(SCHEMA)
        db.execute("CREATE TABLE sumup_transactions(timestamp TEXT NOT NULL)")
        db.execute("INSERT INTO sumup_transactions VALUES('2026-08-19T11:50:00Z')")
        _insert(db, "sumup_transactions")
        for source_id in DERIVED_SOURCE_PARENTS:
            _insert(db, source_id)

    result = capture_recovery_watermark(path, captured_at="2026-08-19T12:00:00Z")
    sources = {row["source_id"]: row for row in result["sources"]}

    assert sources["sumup_transactions"]["classification"] == "ELIGIBLE"
    for source_id, parent in DERIVED_SOURCE_PARENTS.items():
        assert sources[source_id]["classification"] == "DERIVED"
        assert sources[source_id]["derived_from"] == parent
        assert sources[source_id]["watermark_origin"] == "DERIVED_FROM_PARENT"
    assert result["coverage"]["eligible_sources"] == 1
    assert result["coverage"]["measured_sources"] == 1
    assert result["coverage"]["missing_data_max_sources"] == 0
    assert result["coverage"]["derived_sources"] == 3


def test_derived_sources_do_not_inflate_comparison_missing_count(tmp_path):
    path = tmp_path / "drcloud.db"
    with sqlite3.connect(path) as db:
        db.execute(SCHEMA)
        db.execute("CREATE TABLE sumup_transactions(timestamp TEXT NOT NULL)")
        db.execute("INSERT INTO sumup_transactions VALUES('2026-08-19T11:00:00Z')")
        _insert(db, "sumup_transactions")
        for source_id in DERIVED_SOURCE_PARENTS:
            _insert(db, source_id)

    backup = capture_recovery_watermark(path, captured_at="2026-08-19T11:05:00Z")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sumup_transactions VALUES('2026-08-19T11:10:00Z')")
    live = capture_recovery_watermark(path, captured_at="2026-08-19T11:15:00Z")

    comparison = compare_recovery_watermarks(live, backup)
    assert comparison["comparable_sources"] == 1
    assert comparison["unmeasurable_sources"] == 0
    assert comparison["observed_rpo_seconds"] == 600
    assert comparison["confidence"] == "HIGH"
