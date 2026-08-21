import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_reconciliation import reconcile_sumup_payouts_to_bank


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id="p1", reference="BANK REF"):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (payout_id, None, "2026-08-20", "100", "EUR", "0", "PAID", reference, None, None, None, "[]", "{}", "2026-08-20T00:00:00+00:00"))


def test_diagnosis_is_local_only_when_no_qonto_booked_credit(tmp_path):
    path = tmp_path / "db"
    BankLedger(path)
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)
    evidence = reconcile_sumup_payouts_to_bank(path, bank_provider="Qonto")["source_evidence"]
    assert evidence["coverage_diagnosis"] == "NO_LOCAL_QONTO_BOOKED_CREDITS"
    assert evidence["diagnosis_scope"] == "LOCAL_LEDGER_ONLY"
    assert evidence["provider_exhaustiveness_inferred"] is False


def test_diagnosis_reports_missing_bank_references_without_fuzzy_fallback(tmp_path):
    path = tmp_path / "db"
    ledger = BankLedger(path)
    ledger.import_page("Qonto", TransactionPage([
        BankTransaction("a", "2026-08-20T10:00:00+00:00", 100, "EUR", "credit", external_transaction_id="b1", reference=None),
    ], None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)
    evidence = reconcile_sumup_payouts_to_bank(path, bank_provider="Qonto")["source_evidence"]
    assert evidence["coverage_diagnosis"] == "LOCAL_QONTO_BOOKED_CREDIT_REFERENCES_MISSING"
    assert evidence["bank_credits"]["with_reference"] == 0


def test_diagnosis_keeps_exact_match_gap_when_both_reference_coverages_are_complete(tmp_path):
    path = tmp_path / "db"
    ledger = BankLedger(path)
    ledger.import_page("Qonto", TransactionPage([
        BankTransaction("a", "2026-08-20T10:00:00+00:00", 99, "EUR", "credit", external_transaction_id="b1", reference="BANK REF"),
    ], None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)
    result = reconcile_sumup_payouts_to_bank(path, bank_provider="Qonto")
    assert result["matched"] == 0
    assert result["source_evidence"]["coverage_diagnosis"] == "LOCAL_EXACT_MATCH_GAP_REMAINS"


def test_diagnosis_reports_complete_when_all_payouts_match_exactly(tmp_path):
    path = tmp_path / "db"
    ledger = BankLedger(path)
    ledger.import_page("Qonto", TransactionPage([
        BankTransaction("a", "2026-08-20T10:00:00+00:00", 100, "EUR", "credit", external_transaction_id="b1", reference="BANK REF"),
    ], None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)
    result = reconcile_sumup_payouts_to_bank(path, bank_provider="Qonto")
    assert result["coverage_ratio"] == 1
    assert result["unresolved"] == 0 and result["ambiguous"] == 0
    assert result["source_evidence"]["coverage_diagnosis"] == "LOCAL_EXACT_RECONCILIATION_COMPLETE"


def test_non_qonto_provider_diagnosis_is_provider_neutral(tmp_path):
    path = tmp_path / "db"
    BankLedger(path)
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db)
    evidence = reconcile_sumup_payouts_to_bank(path, bank_provider="OtherBank")["source_evidence"]
    assert evidence["coverage_diagnosis"] == "NO_LOCAL_SELECTED_BANK_BOOKED_CREDITS"
    assert evidence["bank_credits"]["provider"] == "OtherBank"
    assert "QONTO" not in evidence["coverage_diagnosis"]
