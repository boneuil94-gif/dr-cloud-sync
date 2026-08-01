import sqlite3
import re
from pathlib import Path

import pytest

from dr_cloud_sync.schema import SchemaMismatchError, ensure_schema
from dr_cloud_sync.sumup import PaymentSettlementLedger, SumUpTransactionLedger


def test_audited_ledgers_never_depend_on_physical_column_order():
    source = Path(__file__).parents[1] / "src" / "dr_cloud_sync"
    audited = ("sumup.py", "sales_ingestion.py", "store.py", "finance.py",
               "purchase_cost.py", "repositories.py")
    bare_insert = re.compile(
        r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s+VALUES\s*\(", re.IGNORECASE
    )
    assert [name for name in audited if bare_insert.search((source / name).read_text())] == []


def test_old_sumup_database_is_fully_migrated_before_sync(tmp_path):
    """Regression: startup migration precedes the first SumUp INSERT."""
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE sumup_transactions(
        sumup_transaction_id TEXT PRIMARY KEY, transaction_code TEXT,
        amount TEXT NOT NULL, currency TEXT NOT NULL, timestamp TEXT NOT NULL,
        fee TEXT NOT NULL, events_json TEXT NOT NULL, raw_json TEXT NOT NULL,
        imported_at TEXT NOT NULL);
      CREATE TABLE sumup_payouts(
        payout_id TEXT PRIMARY KEY, type TEXT, payout_date TEXT NOT NULL,
        amount TEXT NOT NULL, currency TEXT NOT NULL, fee TEXT NOT NULL,
        status TEXT, reference TEXT, raw_json TEXT NOT NULL, imported_at TEXT NOT NULL);
    """)
    db.close()

    transactions = SumUpTransactionLedger(path)
    payouts = PaymentSettlementLedger(path)
    transactions.import_page(type("Page", (), {"rows": ({
        "id": "tx-1", "transaction_code": "code-1", "amount": "12",
        "currency": "EUR", "timestamp": "2026-08-01T10:00:00Z",
        "tip_amount": "1",
    },), "next_cursor": None})())
    payouts.import_page(type("Page", (), {"rows": ({
        "id": "payout-1", "date": "2026-08-02", "amount": "11",
        "currency": "EUR", "fee": "1", "start_date": "2026-08-01",
    },), "next_cursor": None})())

    assert transactions.db.execute(
        "SELECT tip_amount FROM sumup_transactions WHERE sumup_transaction_id=?", ("tx-1",)
    ).fetchone()[0] == "1"
    assert payouts.db.execute(
        "SELECT start_date FROM sumup_payouts WHERE payout_id=?", ("payout-1",)
    ).fetchone()[0] == "2026-08-01"


def test_validator_adds_defaulted_columns_idempotently():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE ledger(id TEXT PRIMARY KEY)")
    schema = "CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY,note TEXT,state TEXT NOT NULL DEFAULT 'OPEN');"
    ensure_schema(db, schema, owner="test ledger")
    ensure_schema(db, schema, owner="test ledger")
    assert [row[1] for row in db.execute("PRAGMA table_info(ledger)")] == ["id", "note", "state"]


def test_validator_diagnoses_unsafe_drift_before_writes():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE ledger(id TEXT PRIMARY KEY)")
    with pytest.raises(SchemaMismatchError, match="missing required column 'amount'"):
        ensure_schema(
            db,
            "CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY,amount TEXT NOT NULL);",
            owner="Purchase Ledger",
        )
    assert db.execute("SELECT count(*) FROM ledger").fetchone()[0] == 0
