import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_amount_gap import amount_gap_funnel


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id, amount, currency, reference, fee='0'):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
        payout_id,None,"2026-08-20",amount,currency,fee,"PAID",reference,None,None,None,"[]","{}","2026-08-20T00:00:00+00:00"
    ))


def test_amount_gap_only_pairs_unique_reference_currency_candidates(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-20T10:00:00+00:00',95,'EUR','one',external_transaction_id='secret-1',reference='PRIVATE A',status='COMPLETED'),
        BankTransaction('a','2026-08-20T11:00:00+00:00',105,'EUR','two',external_transaction_id='secret-2',reference='PRIVATE B',status='COMPLETED'),
        BankTransaction('a','2026-08-20T12:00:00+00:00',90,'EUR','three',external_transaction_id='secret-3',reference='PRIVATE C',status='COMPLETED'),
        BankTransaction('a','2026-08-20T13:00:00+00:00',80,'EUR','four',external_transaction_id='secret-4',reference='PRIVATE D',status='COMPLETED'),
        BankTransaction('a','2026-08-20T14:00:00+00:00',81,'EUR','five',external_transaction_id='secret-5',reference='PRIVATE D',status='COMPLETED'),
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','100','EUR','PRIVATE A','5')
        _payout(db,'p2','100','EUR','PRIVATE B','5')
        _payout(db,'p3','100','EUR','PRIVATE C','5')
        _payout(db,'p4','80','EUR','PRIVATE D','0')
        _payout(db,'p5','50','EUR','NO MATCH','0')
        _payout(db,'p6','50','EUR',None,'0')
    result=amount_gap_funnel(path)
    counts=result['counts']
    assert result['status']=='MEASURABLE'
    assert result['diagnosis_scope']=='LOCAL_LEDGER_ONLY'
    assert result['provider_exhaustiveness_inferred'] is False
    assert result['safety']=={
        'database_read_only':True,
        'provider_network_calls':False,
        'mutations':False,
        'reference_values_emitted':False,
        'row_level_identifiers_emitted':False,
        'monetary_values_emitted':False,
    }
    assert counts=={
        'payouts_total':6,
        'payouts_valid_for_amount_gap':5,
        'payouts_with_reference_currency_overlap':4,
        'payouts_without_reference_currency_bank_candidate':1,
        'payouts_with_unique_reference_currency_bank_candidate':3,
        'payouts_with_multiple_reference_currency_bank_candidates':1,
        'unique_pairs_amount_equal':0,
        'unique_pairs_bank_amount_lower':2,
        'unique_pairs_bank_amount_higher':1,
        'unique_pairs_equal_after_subtracting_payout_fee':1,
        'unique_pairs_equal_after_adding_payout_fee':1,
        'unique_pairs_not_explained_by_payout_fee':1,
        'payouts_with_nonzero_fee':3,
    }
    serialized=str(result)
    assert 'PRIVATE A' not in serialized and 'PRIVATE B' not in serialized and 'PRIVATE C' not in serialized
    assert 'secret-' not in serialized and '95' not in serialized and '105' not in serialized


def test_non_qonto_completed_credit_is_not_treated_as_proven_settled(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('otherbank',TransactionPage([
        BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','other',external_transaction_id='o1',reference='REF',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,'p1','100','EUR','REF')
    result=amount_gap_funnel(path,bank_provider='otherbank')
    assert result['eligible_statuses']==['BOOKED']
    assert result['counts']['payouts_with_reference_currency_overlap']==0


def test_missing_ledger_is_fail_closed_and_not_created(tmp_path):
    path=tmp_path/'missing.db'
    result=amount_gap_funnel(path)
    assert result['status']=='UNMEASURABLE' and result['counts'] is None
    assert result['provider_exhaustiveness_inferred'] is False
    assert not path.exists()
