import sqlite3
from pathlib import Path

from dr_cloud_sync.recovery_watermark import (
    capture_recovery_watermark,
    compare_recovery_watermarks,
)


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
        db.execute("CREATE TABLE purchase_orders(updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE purchase_order_lines(updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE stock_movements(applied_at TEXT,status TEXT NOT NULL)")
    return path


def test_durable_business_ledgers_fill_source_watermarks_read_only(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "shopcaisse_sales", "SHOPCAISSE_SALES")
        _source(db, "prestashop_sales", "PRESTASHOP_SALES")
        _source(db, "sumup_transactions", "SUMUP_TRANSACTIONS", "SumUp")
        _source(db, "bank", "BANK", "Qonto")
        _source(db, "purchases", "PURCHASES", "LOCAL")
        _source(db, "stock", "STOCK", "LOCAL")
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
        db.executemany("INSERT INTO purchase_orders VALUES(?)", [
            ("2026-08-10T07:00:00Z",), ("2026-08-19T10:15:00+00:00",),
        ])
        db.executemany("INSERT INTO stock_movements VALUES(?,?)", [
            ("2026-08-19T09:00:00Z", "APPLIED"),
            ("2026-08-19T11:55:00+00:00", "APPLIED"),
            ("2026-08-19T12:20:00Z", "PENDING"),
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
    assert sources["purchases"]["records_available"] == 2
    assert sources["purchases"]["data_max_at"] == "2026-08-19T10:15:00Z"
    assert sources["stock"]["records_available"] == 2
    assert sources["stock"]["data_max_at"] == "2026-08-19T11:55:00Z"
    assert all(row["classification"] == "ELIGIBLE" for row in sources.values())
    assert all(row["watermark_origin"] == "DURABLE_LEDGER" for row in sources.values())
    assert result["coverage"] == {
        "eligible_sources": 6,
        "measured_sources": 6,
        "missing_data_max_sources": 0,
        "durable_ledger_sources": 6,
        "derived_sources": 0,
    }
    assert result["confidence"] == "MEDIUM"


def test_purchase_projection_includes_mutable_line_watermark(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "purchases", "PURCHASES", "LOCAL")
        db.execute("INSERT INTO purchase_orders VALUES(?)", ("2026-08-19T09:00:00Z",))
        db.execute("INSERT INTO purchase_order_lines VALUES(?)", ("2026-08-19T11:45:00Z",))

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] == 2
    assert source["data_max_at"] == "2026-08-19T11:45:00Z"
    assert source["watermark_table"] == "purchase_orders+purchase_order_lines"
    assert source["classification"] == "ELIGIBLE"


def test_purchase_line_deletion_never_becomes_zero_second_high_confidence_rpo(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "purchases", "PURCHASES", "LOCAL")
        db.execute("INSERT INTO purchase_orders VALUES(?)", ("2026-08-19T09:00:00Z",))
        db.execute("INSERT INTO purchase_order_lines VALUES(?)", ("2026-08-19T09:00:00Z",))
    backup = capture_recovery_watermark(
        path, captured_at="2026-08-19T10:00:00Z", captured_from="BACKUP"
    )
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM purchase_order_lines")
    live = capture_recovery_watermark(
        path, captured_at="2026-08-19T10:05:00Z", captured_from="LIVE"
    )

    comparison = compare_recovery_watermarks(live, backup)
    assert comparison["comparable_sources"] == 0
    assert comparison["unmeasurable_sources"] == 1
    assert comparison["observed_rpo_seconds"] is None
    assert comparison["confidence"] == "UNKNOWN"


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


def test_empty_purchase_and_stock_ledgers_are_no_data_not_unmeasurable(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "purchases", "PURCHASES", "LOCAL", records=99,
                maximum="2026-08-18T00:00:00Z")
        _source(db, "stock", "STOCK", "LOCAL", records=99,
                maximum="2026-08-18T00:00:00Z")
        db.execute("INSERT INTO stock_movements VALUES(?,?)",
                   ("2026-08-19T12:00:00Z", "PENDING"))

    result = capture_recovery_watermark(path)
    sources = {row["source_id"]: row for row in result["sources"]}
    assert sources["purchases"]["records_available"] == 0
    assert sources["purchases"]["classification"] == "NO_DATA"
    assert sources["stock"]["records_available"] == 0
    assert sources["stock"]["classification"] == "NO_DATA"
    assert result["coverage"]["missing_data_max_sources"] == 0


def test_applied_stock_without_durable_applied_timestamp_stays_unmeasurable(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "stock", "STOCK", "LOCAL")
        db.execute("INSERT INTO stock_movements VALUES(NULL,'APPLIED')")

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] == 1
    assert source["data_max_at"] is None
    assert source["classification"] == "UNMEASURABLE"
    assert source["watermark_origin"] == "DURABLE_LEDGER"


def test_mixed_applied_stock_with_any_missing_timestamp_is_unmeasurable(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        _source(db, "stock", "STOCK", "LOCAL")
        db.execute("INSERT INTO stock_movements VALUES('2026-08-19T11:00:00Z','APPLIED')")
        db.execute("INSERT INTO stock_movements VALUES(NULL,'APPLIED')")

    source = capture_recovery_watermark(path)["sources"][0]
    assert source["records_available"] == 2
    assert source["data_min_at"] is None
    assert source["data_max_at"] is None
    assert source["classification"] == "UNMEASURABLE"


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
