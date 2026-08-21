import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_reconciliation import reconcile_sumup_payouts_to_bank


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("p1",None,"2026-08-21","100","EUR","0","PAID","BANK REF",None,None,None,"[]","{}","2026-08-21T00:00:00+00:00"))


def test_qonto_completed_credit_is_reconciliation_eligible(tmp_path):
    path = tmp_path / "db"
    ledger = BankLedger(path)
    tx = BankTransaction(
        "a",
        "2026-08-21T10:00:00+00:00",
        100,
        "EUR",
        "SumUp payout",
        external_transaction_id="qonto-completed",
        reference="BANK REF",
        status="COMPLETED",
    )
    ledger.import_page("qonto", TransactionPage([tx], None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)

    result = reconcile_sumup_payouts_to_bank(path)

    assert result["matched"] == 1
    bank = result["source_evidence"]["bank_credits"]
    assert bank["accepted_statuses"] == ["BOOKED", "COMPLETED"]
    assert bank["eligible_credits_total"] == 1
    assert bank["booked_credits_total"] == 0
    assert bank["completed_credits_total"] == 1
    assert bank["presence"] == "NO_BOOKED_CREDITS"
    assert bank["eligible_presence"] == "ELIGIBLE_CREDITS_PRESENT"


def test_non_qonto_completed_credit_remains_ineligible(tmp_path):
    path = tmp_path / "db"
    ledger = BankLedger(path)
    ledger.import_page(
        "otherbank",
        TransactionPage([
            BankTransaction(
                "a",
                "2026-08-21T10:00:00+00:00",
                100,
                "EUR",
                "SumUp payout",
                external_transaction_id="other-completed",
                reference="BANK REF",
                status="COMPLETED",
            )
        ], None),
    )
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)

    result = reconcile_sumup_payouts_to_bank(path, bank_provider="otherbank")

    assert result["matched"] == 0
    assert result["unresolved"] == 1
    bank = result["source_evidence"]["bank_credits"]
    assert bank["accepted_statuses"] == ["BOOKED"]
    assert bank["eligible_credits_total"] == 0
    assert bank["completed_credits_total"] == 0
