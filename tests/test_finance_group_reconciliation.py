import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_reconciliation import reconcile_sumup_payouts_to_bank


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id, amount, *, currency="EUR", reference="BANK REF", row_type="PAYOUT"):
    db.execute(
        "INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (payout_id,row_type,"2026-08-20",amount,currency,"0","SUCCESSFUL",reference,None,None,None,"[]","{}","2026-08-20T00:00:00+00:00"),
    )


def _bank(path, rows):
    ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage(rows,None))
    return ledger


def test_multi_record_reference_group_matches_only_on_exact_group_sum(tmp_path):
    path=tmp_path/'db'
    tx=BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','SumUp payout',external_transaction_id='b1',reference='BANK REF',status='COMPLETED')
    ledger=_bank(path,[tx])
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','120',row_type='PAYOUT')
        _payout(db,'p2','-20',row_type='REFUND_DEDUCTION')
    result=reconcile_sumup_payouts_to_bank(path)
    bank_id='bank:'+ledger.fingerprint('qonto',tx)
    assert result['matched']==2 and result['unresolved']==0 and result['ambiguous']==0
    assert result['coverage_ratio']==1
    assert result['rows']==[
        {'payout_id':'p1','status':'MATCHED','bank_transaction_id':bank_id},
        {'payout_id':'p2','status':'MATCHED','bank_transaction_id':bank_id},
    ]


def test_grouping_does_not_replace_individual_fail_closed_contention(tmp_path):
    path=tmp_path/'db'
    tx=BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','SumUp payout',external_transaction_id='b1',reference='BANK REF')
    _bank(path,[tx])
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','100')
        _payout(db,'p2','100')
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['ambiguous']==2 and result['coverage_ratio']==0
    assert {row['reason'] for row in result['rows']}=={'BANK_CREDIT_CONTENDED'}


def test_multiple_exact_group_bank_candidates_are_ambiguous(tmp_path):
    path=tmp_path/'db'
    rows=[
        BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK REF'),
        BankTransaction('a','2026-08-20T11:00:00+00:00',100,'EUR','two',external_transaction_id='b2',reference='BANK REF'),
    ]
    _bank(path,rows)
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','120')
        _payout(db,'p2','-20')
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['ambiguous']==2
    assert {row['reason'] for row in result['rows']}=={'MULTIPLE_EXACT_GROUP_BANK_MATCHES'}


def test_group_sum_never_crosses_reference_or_currency(tmp_path):
    path=tmp_path/'db'
    _bank(path,[BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK REF')])
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','120',reference='BANK REF')
        _payout(db,'p2','-20',reference='OTHER REF')
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['unresolved']==2


def test_invalid_group_member_is_not_summed_into_a_match(tmp_path):
    path=tmp_path/'db'
    tx=BankTransaction('a','2026-08-20T10:00:00+00:00',120,'EUR','one',external_transaction_id='b1',reference='BANK REF')
    _bank(path,[tx])
    with sqlite3.connect(path) as db:
        _sumup_schema(db)
        _payout(db,'p1','120')
        _payout(db,'p2','sNaN')
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==1 and result['unresolved']==1
    assert next(row for row in result['rows'] if row['payout_id']=='p2')['reason']=='PAYOUT_AMOUNT_OR_CURRENCY_INVALID'
