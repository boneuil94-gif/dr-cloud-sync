import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_group_fee_gap import group_fee_gap_funnel


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id, amount, reference, fee='0'):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
        payout_id,None,"2026-08-22",amount,"EUR",fee,"PAID",reference,None,None,None,"[]","{}","2026-08-22T00:00:00+00:00"
    ))


def test_group_fee_gap_classifies_exact_total_fee_without_exposing_values(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-22T10:00:00+00:00',105,'EUR','bank',external_transaction_id='secret-bank-id',reference='PRIVATE REF',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','40','PRIVATE REF','2')
        _payout(db,'p2','60','PRIVATE REF','3')
    result=group_fee_gap_funnel(path)
    counts=result['counts']
    assert result['status']=='MEASURABLE'
    assert counts['multi_record_groups_total']==1
    assert counts['multi_record_groups_with_unique_bank_candidate']==1
    assert counts['unique_candidate_groups_bank_amount_higher']==1
    assert counts['unique_candidate_groups_with_nonzero_total_fee']==1
    assert counts['unique_candidate_groups_equal_after_adding_total_fee']==1
    assert counts['unique_candidate_groups_equal_after_subtracting_total_fee']==0
    assert counts['unique_candidate_groups_not_explained_by_total_fee']==0
    serialized=str(result)
    assert 'PRIVATE REF' not in serialized and 'secret-bank-id' not in serialized
    assert '105' not in serialized and '40' not in serialized and '60' not in serialized
    assert result['safety']['monetary_values_emitted'] is False


def test_group_fee_gap_invalid_fee_is_fail_closed(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-22T10:00:00+00:00',105,'EUR','bank',external_transaction_id='b1',reference='REF',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','40','REF','2')
        _payout(db,'p2','60','REF','sNaN')
    counts=group_fee_gap_funnel(path)['counts']
    assert counts['unique_candidate_groups_with_invalid_payout_fee']==1
    assert counts['unique_candidate_groups_equal_after_adding_total_fee']==0
    assert counts['unique_candidate_groups_not_explained_by_total_fee']==0


def test_group_fee_gap_invalid_amount_is_fail_closed(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-22T10:00:00+00:00',100,'EUR','bank',external_transaction_id='b2',reference='REF',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','40','REF','0')
        _payout(db,'p2','sNaN','REF','0')
    counts=group_fee_gap_funnel(path)['counts']
    assert counts['unique_candidate_groups_with_invalid_payout_amount']==1
    assert counts['unique_candidate_groups_exact_amount_equal']==0


def test_group_fee_gap_requires_unique_bank_candidate(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-22T10:00:00+00:00',100,'EUR','one',external_transaction_id='b3',reference='REF',status='COMPLETED'),
        BankTransaction('a','2026-08-22T11:00:00+00:00',100,'EUR','two',external_transaction_id='b4',reference='REF',status='COMPLETED'),
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','40','REF','0')
        _payout(db,'p2','60','REF','0')
    counts=group_fee_gap_funnel(path)['counts']
    assert counts['multi_record_groups_with_multiple_bank_candidates']==1
    assert counts['multi_record_groups_with_unique_bank_candidate']==0


def test_group_fee_gap_invalid_bank_candidate_amount_blocks_uniqueness(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-22T10:00:00+00:00',105,'EUR','one',external_transaction_id='b5',reference='REF',status='COMPLETED'),
        BankTransaction('a','2026-08-22T11:00:00+00:00',1,'EUR','two',external_transaction_id='b6',reference='REF',status='COMPLETED'),
    ],None))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE bank_transactions SET amount='sNaN' WHERE external_transaction_id='b6'")
        _sumup_schema(db)
        _payout(db,'p1','40','REF','2')
        _payout(db,'p2','60','REF','3')
    counts=group_fee_gap_funnel(path)['counts']
    assert counts['multi_record_groups_with_invalid_bank_candidate_amount']==1
    assert counts['multi_record_groups_with_unique_bank_candidate']==0
    assert counts['unique_candidate_groups_equal_after_adding_total_fee']==0


def test_missing_ledger_is_fail_closed(tmp_path):
    path=tmp_path/'missing.db'
    result=group_fee_gap_funnel(path)
    assert result['status']=='UNMEASURABLE' and result['counts'] is None
    assert result['provider_exhaustiveness_inferred'] is False
    assert not path.exists()
