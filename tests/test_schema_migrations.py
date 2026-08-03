import sqlite3
import re
from pathlib import Path

import pytest

from dr_cloud_sync.schema import SchemaMismatchError, ensure_schema
from dr_cloud_sync.sumup import PaymentSettlementLedger, SumUpTransactionLedger
from dr_cloud_sync.sumup_migrations import SUMUP_SCHEMA_VERSION, sumup_schema_diagnostic


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


def test_exact_production_regressions_preserve_rows_and_replay(tmp_path):
    """The observed 16/12-column production shapes upgrade without replacement."""
    path = tmp_path / "production.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE sumup_transactions(
        sumup_transaction_id TEXT PRIMARY KEY,transaction_code TEXT,amount TEXT NOT NULL,
        currency TEXT NOT NULL,timestamp TEXT NOT NULL,status TEXT,payment_type TEXT,
        entry_mode TEXT,vat_amount TEXT,tip_amount TEXT,foreign_transaction_id TEXT,
        client_transaction_id TEXT,fee TEXT NOT NULL,events_json TEXT NOT NULL,
        raw_json TEXT NOT NULL,imported_at TEXT NOT NULL);
      CREATE TABLE sumup_payouts(
        payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,
        currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,
        transaction_code TEXT,deductions_json TEXT NOT NULL,raw_json TEXT NOT NULL,
        imported_at TEXT NOT NULL);
      INSERT INTO sumup_transactions(sumup_transaction_id,amount,currency,timestamp,fee,
        events_json,raw_json,imported_at) VALUES('legacy-tx','4','EUR','2026-01-01','0','[]','{}','then');
      INSERT INTO sumup_payouts(payout_id,payout_date,amount,currency,fee,deductions_json,
        raw_json,imported_at) VALUES('legacy-pay','2026-01-02','4','EUR','0','[]','{}','then');
    """)
    db.close()

    transactions = SumUpTransactionLedger(path)
    first = transactions.schema_migration
    transactions.db.close()
    payouts = PaymentSettlementLedger(path)
    second = payouts.schema_migration
    assert first["added_columns_this_start"]
    assert second["added_columns_this_start"] == []
    assert second["schema_version"] == SUMUP_SCHEMA_VERSION
    assert second["pending_migrations"] == []
    assert payouts.db.execute("SELECT amount FROM sumup_transactions WHERE sumup_transaction_id='legacy-tx'").fetchone()[0] == "4"
    assert payouts.db.execute("SELECT amount FROM sumup_payouts WHERE payout_id='legacy-pay'").fetchone()[0] == "4"
    assert payouts.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    page = type("Page", (), {"rows": ({"id": "new-pay", "date": "2026-01-03", "amount": "4"},), "next_cursor": None})()
    payouts.import_page(page)
    assert payouts.db.execute("SELECT count(*) FROM sumup_payouts").fetchone()[0] == 2
    # These exact SQLite exceptions were the production regression. Explicit
    # columns plus the startup migration make both structurally impossible.
    errors = ("table sumup_transactions has no column named simple_status",
              "table sumup_payouts has 12 columns but 13 values were supplied")
    assert all(message not in str(first) + str(second) for message in errors)
    assert sumup_schema_diagnostic(payouts.db)["last_check"]["result"] == "OK"
    payouts.db.close()


def test_fresh_sumup_database_contains_every_declared_ledger(tmp_path):
    ledger = SumUpTransactionLedger(tmp_path / "fresh.sqlite")
    tables = {row[0] for row in ledger.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sumup_transactions", "sumup_payouts", "sumup_transaction_events", "sumup_fees",
            "sumup_refunds", "sumup_chargebacks", "sumup_payout_items",
            "payment_settlements", "sumup_schema_migrations"} <= tables
    assert ledger.schema_migration["schema_version"] == SUMUP_SCHEMA_VERSION


def test_web_and_worker_share_startup_factory_database_and_volume():
    root = Path(__file__).parents[1]
    cli = (root / "src/dr_cloud_sync/cli.py").read_text()
    web = (root / "src/dr_cloud_sync/inventory_web.py").read_text()
    compose = (root / "deploy/ovh/docker-compose.yml").read_text()
    assert 'settings=OSSettings.from_env(); app=create_app(settings)' in cli
    assert "InventoryRepository(settings.database)" in web
    assert compose.count("- drcloud-data:/data") == 2


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


def test_diagnostic_proves_roles_resolve_same_file_and_schema(tmp_path):
    from dr_cloud_sync.sqlite_diagnostics import register_runtime, runtime_diagnostics
    path = tmp_path / "shared.sqlite"
    ledger = SumUpTransactionLedger(path)
    register_runtime(ledger.db, "automation-worker")
    register_runtime(ledger.db, "migration-command")
    diagnostics = runtime_diagnostics(ledger.db)
    assert {item["role"] for item in diagnostics} == {"web", "automation-worker", "migration-command"}
    assert len({item["absolute_path"] for item in diagnostics}) == 1
    assert len({item["sha256"] for item in diagnostics}) == 1
    assert {item["user_version"] for item in diagnostics} == {SUMUP_SCHEMA_VERSION}
    assert all("simple_status" in {row[1] for row in item["sumup_transactions"]} for item in diagnostics)
    assert all("deductions_json" in {row[1] for row in item["sumup_payouts"]} for item in diagnostics)


def test_recorded_ok_migration_cannot_hide_missing_simple_status(tmp_path):
    path=tmp_path/"false-green.sqlite";db=sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE sumup_transactions(sumup_transaction_id TEXT PRIMARY KEY);
      CREATE TABLE sumup_schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT);
      INSERT INTO sumup_schema_migrations VALUES(1,'old-ok','then');
    """);db.close()
    ledger=SumUpTransactionLedger(path)
    assert "simple_status" in {row[1] for row in ledger.db.execute("PRAGMA table_info(sumup_transactions)")}
    assert ledger.schema_migration["schema_version"] == SUMUP_SCHEMA_VERSION


def test_deductions_json_has_database_and_writer_defaults(tmp_path):
    ledger=PaymentSettlementLedger(tmp_path/"deductions.sqlite")
    info={row[1]:row for row in ledger.db.execute("PRAGMA table_info(sumup_payouts)")}
    assert info["deductions_json"][3] == 1 and info["deductions_json"][4] == "'[]'"
    ledger.import_page(type("Page",(),{"rows":({"id":"p","date":"2026-08-03","amount":"1"},),"next_cursor":None})())
    assert ledger.db.execute("SELECT deductions_json FROM sumup_payouts").fetchone()[0] == "[]"
