import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_match_funnel import exact_match_funnel


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id, amount, currency, reference):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
        payout_id,None,"2026-08-20",amount,currency,"0","PAID",reference,None,None,None,"[]","{}","2026-08-20T00:00:00+00:00"
    ))


def test_funnel_counts_only_progressive_exact_match_stages(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='secret-bank-1',reference='PRIVATE A',status='COMPLETED'),
        BankTransaction('a','2026-08-20T11:00:00+00:00',200,'USD','two',external_transaction_id='secret-bank-2',reference='PRIVATE B',status='COMPLETED'),
        BankTransaction('a','2026-08-20T12:00:00+00:00',300,'EUR','three',external_transaction_id='secret-bank-3',reference='PRIVATE C',status='COMPLETED'),
        BankTransaction('a','2026-08-20T13:00:00+00:00',300,'EUR','four',external_transaction_id='secret-bank-4',reference='PRIVATE C',status='COMPLETED'),
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','100','EUR',' private   a ')
        _payout(db,'p2','200','EUR','PRIVATE B')
        _payout(db,'p3','999','EUR','PRIVATE C')
        _payout(db,'p4','300','EUR','PRIVATE C')
        _payout(db,'p5','50','EUR','NO BANK REF')
        _payout(db,'p6','50','EUR',None)
    result=exact_match_funnel(path)
    counts=result['counts']
    assert result['status']=='MEASURABLE'
    assert result['diagnosis_scope']=='LOCAL_LEDGER_ONLY'
    assert result['provider_exhaustiveness_inferred'] is False
    assert counts=={
        'payouts_total':6,
        'payouts_valid_for_exact_matching':5,
        'eligible_bank_credits_total':4,
        'eligible_bank_credits_valid_for_exact_matching':4,
        'payouts_with_reference_overlap':4,
        'payouts_with_reference_currency_overlap':3,
        'payouts_with_exact_tuple_overlap':2,
        'payouts_with_unique_exact_bank_candidate':1,
        'payouts_with_multiple_exact_bank_candidates':1,
    }
    serialized=str(result)
    assert 'PRIVATE A' not in serialized and 'PRIVATE B' not in serialized and 'PRIVATE C' not in serialized
    assert 'secret-bank' not in serialized


def test_qonto_completed_is_included_but_unproven_provider_completed_is_not(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    qonto=BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','q',external_transaction_id='q1',reference='REF',status='COMPLETED')
    other=BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','o',external_transaction_id='o1',reference='REF',status='COMPLETED')
    ledger.import_page('Qonto',TransactionPage([qonto],None)); ledger.import_page('otherbank',TransactionPage([other],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,'p1','100','EUR','REF')
    assert exact_match_funnel(path,bank_provider='qonto')['counts']['payouts_with_unique_exact_bank_candidate']==1
    other_result=exact_match_funnel(path,bank_provider='otherbank')
    assert other_result['eligible_statuses']==['BOOKED']
    assert other_result['counts']['eligible_bank_credits_total']==0


def test_missing_ledger_is_fail_closed_and_not_created(tmp_path):
    path=tmp_path/'missing.db'
    result=exact_match_funnel(path)
    assert result['status']=='UNMEASURABLE' and result['counts'] is None
    assert result['provider_exhaustiveness_inferred'] is False
    assert not path.exists()
