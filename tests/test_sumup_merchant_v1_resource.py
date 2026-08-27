import io
import json
import sqlite3

import pytest

from dr_cloud_sync.sumup import SumUpError, SumUpProvider, SumUpTransactionLedger
from dr_cloud_sync.sumup_migrations import SUMUP_SCHEMA_VERSION


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Secrets:
    def get(self, ref):
        return "top-secret"


def test_merchant_uses_documented_v1_read_only_resource():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return Response(json.dumps({
            "merchant_code": "MERCHANT",
            "created_at": "2026-08-01T09:00:00Z",
            "updated_at": "2026-08-27T09:00:00+00:00",
        }).encode())

    provider = SumUpProvider("MERCHANT", "sumup.production", Secrets(), opener=opener, retries=1)
    payload = provider.merchant()

    assert payload["merchant_code"] == "MERCHANT"
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].full_url == "https://api.sumup.com/v1/merchants/MERCHANT"
    assert calls[0].headers["Authorization"] == "Bearer top-secret"


def test_merchant_timestamps_are_typed_and_raw_payload_stays_sanitized(tmp_path):
    ledger = SumUpTransactionLedger(tmp_path / "db.sqlite")
    result = ledger.import_merchant({
        "merchant_code": "MERCHANT",
        "company_name": "Dr Cloud",
        "created_at": "2026-08-01T09:00:00Z",
        "updated_at": "2026-08-27T09:00:00+02:00",
        "access_token": "must-not-persist",
    })

    row = ledger.db.execute(
        "SELECT merchant_code,created_at,updated_at,raw_json FROM sumup_merchants"
    ).fetchone()
    assert result == {
        "merchant_code": "MERCHANT",
        "created_at": "2026-08-01T09:00:00Z",
        "updated_at": "2026-08-27T09:00:00+02:00",
    }
    assert row["merchant_code"] == "MERCHANT"
    assert row["created_at"] == "2026-08-01T09:00:00Z"
    assert row["updated_at"] == "2026-08-27T09:00:00+02:00"
    assert "must-not-persist" not in row["raw_json"]


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
@pytest.mark.parametrize("bad", [None, "", "2026-08-27 09:00:00Z", "2026-08-27T09:00:00", "not-a-time"])
def test_merchant_timestamp_contract_fails_closed(field, bad, tmp_path):
    ledger = SumUpTransactionLedger(tmp_path / f"{field}.sqlite")
    payload = {
        "merchant_code": "MERCHANT",
        "created_at": "2026-08-01T09:00:00Z",
        "updated_at": "2026-08-27T09:00:00Z",
    }
    payload[field] = bad

    with pytest.raises(SumUpError) as caught:
        ledger.import_merchant(payload)

    assert caught.value.diagnostic["stage"] == "merchant_profile"
    assert caught.value.diagnostic["category"] == "PARSING"
    assert caught.value.diagnostic["field"] == field
    assert ledger.db.execute("SELECT count(*) FROM sumup_merchants").fetchone()[0] == 0


def test_merchant_timestamp_migration_is_additive_idempotent(tmp_path):
    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE sumup_merchants(
        merchant_code TEXT PRIMARY KEY,legal_name TEXT,trading_name TEXT,country TEXT,
        currency TEXT,timezone TEXT,status TEXT,payout_settings_json TEXT NOT NULL,
        raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)""")
    db.execute(
        "INSERT INTO sumup_merchants VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("LEGACY", None, None, None, None, None, None, "{}", "{}", "then"),
    )
    db.commit()
    db.close()

    first = SumUpTransactionLedger(path)
    columns = {row[1] for row in first.db.execute("PRAGMA table_info(sumup_merchants)")}
    assert {"created_at", "updated_at"} <= columns
    assert first.db.execute("SELECT merchant_code FROM sumup_merchants").fetchone()[0] == "LEGACY"
    assert first.schema_migration["schema_version"] == SUMUP_SCHEMA_VERSION
    first.db.close()

    second = SumUpTransactionLedger(path)
    assert second.schema_migration["global_status"] == "OK"
    assert second.schema_migration["added_columns_this_start"] == []
    assert second.db.execute("SELECT merchant_code FROM sumup_merchants").fetchone()[0] == "LEGACY"
    second.db.close()
