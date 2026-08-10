from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.financial_reconciliation import FinancialReconciliationService
from dr_cloud_sync.sumup import PaymentSettlementLedger, SumUpPage


NOW=datetime(2026,8,10,12,tzinfo=timezone.utc)


def services(tmp_path):
    bank=BankLedger(tmp_path/"finance.sqlite")
    payouts=PaymentSettlementLedger(bank.db)
    reconciliation=FinancialReconciliationService(bank.db)
    return bank,payouts,reconciliation


def payout(payouts, pid="pay-1", amount="100", date=NOW, currency="EUR", fee=None):
    row={"id":pid,"payout_date":date.isoformat(),"amount":amount,"currency":currency,"status":"PAID"}
    if fee is not None: row["fee"]=fee
    payouts.import_page(SumUpPage((row,),None))


def credit(bank, tid="bank-1", amount="100", date=NOW, currency="EUR", label="SumUp payout", account="account-1", reference=None):
    bank.import_page("Qonto",TransactionPage((BankTransaction(account,date.isoformat(),Decimal(amount),currency,label,external_transaction_id=tid,counterparty="SumUp Payments" if "sumup" in label.casefold() else None,reference=reference),),None))


def test_exact_payout_qonto_and_idempotent_import(tmp_path):
    bank,payouts,service=services(tmp_path); payout(payouts); credit(bank,reference="pay-1")
    assert credit(bank,reference="pay-1") is None
    result=service.recompute()
    assert (result["bank_transactions_available"],result["payouts_matched"]) == (1,1)
    assert service.matches()["items"][0]["status"] == "MATCHED"


def test_shifted_date_is_probable_and_human_decision_survives_recompute(tmp_path):
    bank,payouts,service=services(tmp_path); payout(payouts); credit(bank,date=NOW+timedelta(days=2))
    service.recompute(); match=service.matches()["items"][0]
    assert match["status"] == "PROBABLE" and match["date_difference_seconds"] == 172800
    service.review(match["match_id"],"CONFIRM","auditor@example.test"); service.recompute()
    saved=service.matches()["items"][0]
    assert saved["status"] == "MATCHED" and saved["decision_source"] == "HUMAN"
    history=bank.db.execute("SELECT old_status,new_status,decided_by FROM finance_match_decisions").fetchone()
    assert tuple(history)==("PROBABLE","MATCHED","auditor@example.test")


def test_amount_difference_is_unmatched_and_zero_is_real(tmp_path):
    bank,payouts,service=services(tmp_path); payout(payouts,amount="100"); credit(bank,amount="99")
    payout(payouts,pid="zero",amount="0"); credit(bank,tid="zero-bank",amount="0",reference="zero")
    result=service.recompute()
    assert result["payouts_matched"]==1 and result["payouts_unmatched"]==1
    assert service.evidence()["reconciliation_coverage"]["value"]=="0.5"


def test_two_candidates_are_ambiguous_and_new_bank_recalculates(tmp_path):
    bank,payouts,service=services(tmp_path); payout(payouts)
    service.recompute(); assert service.evidence()["payouts_unmatched"]==1
    credit(bank,"one",date=NOW-timedelta(days=1)); credit(bank,"two",date=NOW+timedelta(days=1))
    service.recompute(); assert service.evidence()["payouts_ambiguous"]==1
    assert any(x["type"]=="AMBIGUOUS_RECONCILIATION" for x in service.anomalies("OPEN")["items"])


def test_nulls_multiple_accounts_unknown_fee_and_secret_filter(tmp_path):
    bank,payouts,service=services(tmp_path); payout(payouts,fee=None)
    credit(bank,"a",account="account-a",label="ordinary credit",reference=None)
    credit(bank,"b",account="account-b",amount="5",label="ordinary credit")
    service.recompute(); ledger=service.ledger()["items"]
    assert {x["bank_account_id"] for x in ledger}=={"account-a","account-b"}
    assert all(x["value_date"] is None for x in ledger)
    assert all(x["classification"]=="UNKNOWN" for x in ledger)
    assert "secret" not in str(service.matches()).casefold()


def test_legacy_sqlite_additive_migration_and_thousands_performance_shape(tmp_path):
    path=tmp_path/"legacy.sqlite"; bank=BankLedger(path); payouts=PaymentSettlementLedger(bank.db)
    for i in range(3000): credit(bank,str(i),amount=str(i+1),date=NOW+timedelta(seconds=i),account=f"a-{i%2}",label="ordinary")
    payout(payouts,amount="2500",date=NOW+timedelta(seconds=2499))
    service=FinancialReconciliationService(bank.db); service.recompute()
    assert service.evidence()["bank_transactions_available"]==3000
    plans=" ".join(str(tuple(x)) for x in bank.db.execute("EXPLAIN QUERY PLAN SELECT * FROM bank_transactions WHERE direction='CREDIT' AND currency='EUR' AND amount='2500' AND booked_at BETWEEN ? AND ?",(NOW.isoformat(),(NOW+timedelta(days=1)).isoformat())))
    assert "idx_bank_tx_match" in plans
