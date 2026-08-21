import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_reconciliation import reconcile_sumup_payouts_to_bank


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id="p1", amount="100", currency="EUR", reference="BANK REF"):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(payout_id,None,"2026-08-20",amount,currency,"0","PAID",reference,None,None,None,"[]","{}","2026-08-20T00:00:00+00:00"))


def test_exact_reference_amount_currency_matches_one_bank_credit(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    tx=BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','SumUp payout',external_transaction_id='b1',reference='  bank   ref  ')
    ledger.import_page('qonto',TransactionPage([tx],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db)
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['status']=='MEASURABLE' and result['matched']==1 and result['coverage_ratio']==1
    assert result['rows']==[{'payout_id':'p1','status':'MATCHED','bank_transaction_id':'bank:'+ledger.fingerprint('qonto',tx)}]


def test_missing_reference_never_uses_amount_only_fallback(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','SumUp payout',external_transaction_id='b1',reference='whatever')],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,reference=None)
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['unresolved']==1
    assert result['rows'][0]['reason']=='PAYOUT_REFERENCE_MISSING'


def test_multiple_exact_bank_candidates_are_ambiguous(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    rows=[
        BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK REF'),
        BankTransaction('a','2026-08-20T11:00:00+00:00',100,'EUR','two',external_transaction_id='b2',reference='BANK REF'),
    ]
    ledger.import_page('qonto',TransactionPage(rows,None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db)
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['ambiguous']==1
    assert result['rows'][0]['reason']=='MULTIPLE_EXACT_BANK_MATCHES'


def test_one_bank_credit_cannot_match_multiple_payouts(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK REF')],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db); _payout(db,'p1'); _payout(db,'p2')
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['ambiguous']==2 and result['coverage_ratio']==0
    assert {row['reason'] for row in result['rows']}=={'BANK_CREDIT_CONTENDED'}


def test_currency_or_amount_mismatch_stays_unresolved(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([BankTransaction('a','2026-08-20T10:00:00+00:00',99,'EUR','one',external_transaction_id='b1',reference='BANK REF')],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db)
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['unresolved']==1 and result['rows'][0]['reason']=='NO_EXACT_BANK_MATCH'


def test_non_finite_money_is_unresolved_not_matched_or_crashing(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK REF')],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,amount='sNaN')
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['matched']==0 and result['unresolved']==1
    assert result['rows'][0]['reason']=='PAYOUT_AMOUNT_OR_CURRENCY_INVALID'


def test_missing_ledgers_fail_closed_without_creating_database(tmp_path):
    missing=tmp_path/'missing.db'
    result=reconcile_sumup_payouts_to_bank(missing)
    assert result['status']=='UNMEASURABLE' and result['source_evidence'] is None
    assert not missing.exists()


def test_empty_payouts_are_no_data(tmp_path):
    path=tmp_path/'empty.db'; BankLedger(path)
    with sqlite3.connect(path) as db: _sumup_schema(db)
    result=reconcile_sumup_payouts_to_bank(path)
    assert result['status']=='NO_DATA' and result['coverage_ratio'] is None and result['payouts_total']==0
    assert result['source_evidence']['payouts']['reference_coverage_ratio'] is None


def test_source_evidence_is_aggregate_and_explains_bank_presence_reference_and_range(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-18T10:00:00+00:00',100,'EUR','one',external_transaction_id='secret-bank-id-1',reference='PRIVATE REF'),
        BankTransaction('a','2026-08-20T11:00:00+00:00',50,'EUR','two',external_transaction_id='secret-bank-id-2',reference=None),
    ],None))
    with sqlite3.connect(path) as db:
        _sumup_schema(db); _payout(db,'p1',reference='PRIVATE REF'); _payout(db,'p2',reference=None)
    result=reconcile_sumup_payouts_to_bank(path)
    evidence=result['source_evidence']
    assert evidence['payouts']['total']==2 and evidence['payouts']['with_reference']==1
    assert evidence['payouts']['without_reference']==1 and evidence['payouts']['reference_coverage_ratio']==0.5
    bank=evidence['bank_credits']
    assert bank['provider']=='Qonto' and bank['presence']=='BOOKED_CREDITS_PRESENT'
    assert bank['booked_credits_total']==2 and bank['with_reference']==1 and bank['without_reference']==1
    assert bank['reference_coverage_ratio']==0.5
    assert bank['booked_at_min']=='2026-08-18T10:00:00+00:00' and bank['booked_at_max']=='2026-08-20T11:00:00+00:00'
    assert bank['latest_imported_at'] is not None and evidence['payouts']['latest_imported_at']=='2026-08-20T00:00:00+00:00'
    serialized=str(evidence)
    assert 'PRIVATE REF' not in serialized and 'secret-bank-id' not in serialized
