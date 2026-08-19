import sqlite3
from pathlib import Path

from dr_cloud_sync.recovery_watermark import capture_recovery_watermark


DATA_SOURCES = """CREATE TABLE data_sources(
 source_id TEXT PRIMARY KEY, source_type TEXT, provider TEXT, status TEXT,
 enabled INTEGER, last_success_at TEXT, stale_after_seconds INTEGER,
 data_min_at TEXT, data_max_at TEXT, records_available INTEGER)"""


def _source(db, source_id, source_type, provider="provider", *,
            records=None, minimum=None, maximum=None, status="CONNECTED"):
    db.execute(
        "INSERT INTO data_sources VALUES(?,?,?,?,?,?,?,?,?,?)",
        (source_id, source_type, provider, status, 1,
         "2026-08-19T12:00:00Z", 3600, minimum, maximum, records),
    )


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "drcloud.db"
    with sqlite3.connect(path) as db:
        db.execute(DATA_SOURCES)
        db.execute("CREATE TABLE sales(source TEXT NOT NULL,sold_at TEXT NOT NULL)")
        db.execute("CREATE TABLE sumup_transactions(timestamp TEXT NOT NULL)")
        db.execute("CREATE TABLE bank_transactions(booked_at TEXT NOT NULL)")
    return path


def test_durable_business_ledgers_fill_source_watermarks_read_only(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "shopcaisse_sales", "SHOPCAISSE_SALES")
        _source(db, "prestashop_sales", "PRESTASHOP_SALES")
        _source(db, "sumup_transactions", "SUMUP_TRANSACTIONS", "SumUp")
        _source(db, "bank", "BANK", "Qonto")
        db.executemany("INSERT INTO sales VALUES(?,?)", [
            ("SHOPCAISSE", "2026-08-18T10:00:00+00:00"),
            ("SHOPCAISSE", "2026-08-19T11:30:00Z"),
            ("PRESTASHOP", "2026-08-17T09:00:00Z"),
        ])
        db.executemany("INSERT INTO sumup_transactions VALUES(?)", [
            ("2026-08-15T08:00:00Z",), ("2026-08-19T11:45:00+00:00",),
        ])
        db.executemany("INSERT INTO bank_transactions VALUES(?)", [
            ("2026-08-01T08:00:00Z",), ("2026-08-19T05:24:48Z",),
        ])

    result = capture_recovery_watermark(
        path, captured_at="2026-08-19T12:30:00Z", captured_from="TEST"
    )
    sources = {row["source_id"]: row for row in result["sources"]}

    assert sources["shopcaisse_sales"]["records_available"] == 2
    assert sources["shopcaisse_sales"]["data_min_at"] == "2026-08-18T10:00:00Z"
    assert sources["shopcaisse_sales"]["data_max_at"] == "2026-08-19T11:30:00Z"
    assert sources["prestashop_sales"]["records_available"] == 1
    assert sources["sumup_transactions"]["records_available"] == 2
    assert sources["sumup_transactions"]["data_max_at"] == "2026-08-19T11:45:00Z"
    assert sources["bank"]["records_available"] == 2
    assert all(row["classification"] == "ELIGIBLE" for row in sources.values())
    assert all(row["watermark_origin"] == "DURABLE_LEDGER" for row in sources.values())
    assert result["coverage"] == {
        "eligible_sources": 4,
        "measured_sources": 4,
        "missing_data_max_sources": 0,
        "durable_ledger_sources": 4,
    }
    assert result["confidence"] == "MEDIUM"


def test_empty_durable_ledger_clears_stale_control_plane_watermark(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "shopcaisse_sales", "SHOPCAISSE_SALES", records=99,
                minimum="2025-01-01T00:00:00Z", maximum="2026-08-19T10:00:00Z")

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] == 0
    assert source["data_min_at"] is None and source["data_max_at"] is None
    assert source["classification"] == "NO_DATA"
    assert source["watermark_origin"] == "DURABLE_LEDGER"


def test_unapproved_source_keeps_control_plane_watermark(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "custom_source", "CUSTOM", records=7,
                minimum="2026-08-01T00:00:00Z", maximum="2026-08-19T09:00:00Z")

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] == 7
    assert source["data_max_at"] == "2026-08-19T09:00:00Z"
    assert source["watermark_origin"] == "DATA_SOURCE_CONTROL_PLANE"
    assert source["classification"] == "ELIGIBLE"


def test_invalid_durable_business_timestamp_is_never_promoted(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "sumup_transactions", "SUMUP_TRANSACTIONS", "SumUp")
        db.execute("INSERT INTO sumup_transactions VALUES('2026-08-19')")

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] == 1
    assert source["data_max_at"] == "2026-08-19"
    assert source["classification"] == "INVALID_TIMESTAMP"
    assert source["watermark_origin"] == "DURABLE_LEDGER"


def test_missing_expected_ledger_fails_back_without_guessing(tmp_path):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as db:
        db.execute(DATA_SOURCES)
        _source(db, "sumup_transactions", "SUMUP_TRANSACTIONS", "SumUp")

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] is None
    assert source["data_max_at"] is None
    assert source["classification"] == "UNMEASURABLE"
    assert source["watermark_origin"] == "DATA_SOURCE_CONTROL_PLANE"
