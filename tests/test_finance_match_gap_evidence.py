import sqlite3

from dr_cloud_sync.bank import BankLedger, BankTransaction, TransactionPage
from dr_cloud_sync.finance_match_gap_evidence import local_exact_match_gap_evidence


def _sumup_schema(db):
    db.execute("CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,start_date TEXT,end_date TEXT,paid_date TEXT,deductions_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)")


def _payout(db, payout_id="p1", amount="100", currency="EUR", reference="BANK REF"):
    db.execute("INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(payout_id,None,"2026-08-20",amount,currency,"0","PAID",reference,None,None,None,"[]","{}","2026-08-20T00:00:00+00:00"))


def test_exact_triplet_overlap_is_counted_without_emitting_values(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','secret label',external_transaction_id='secret-id',reference=' private   ref ',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,reference='PRIVATE REF')
    evidence=local_exact_match_gap_evidence(path)
    counts=evidence['counts']
    assert evidence['status']=='MEASURABLE' and evidence['diagnosis']=='EXACT_TRIPLET_OVERLAP_PRESENT'
    assert counts['exact_triplet_overlap_payouts']==1 and counts['reference_overlap_payouts']==1
    assert counts['amount_currency_overlap_payouts']==1
    serialized=str(evidence)
    assert 'PRIVATE REF' not in serialized and 'secret-id' not in serialized and 'secret label' not in serialized
    assert evidence['safety']['database_read_only'] is True and evidence['safety']['provider_network_calls'] is False


def test_reference_domain_gap_is_distinguished_from_amount_currency_overlap(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK A',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,reference='BANK B')
    evidence=local_exact_match_gap_evidence(path)
    assert evidence['diagnosis']=='REFERENCE_DOMAIN_GAP'
    assert evidence['counts']['reference_overlap_payouts']==0
    assert evidence['counts']['amount_currency_overlap_payouts']==1
    assert evidence['counts']['exact_triplet_overlap_payouts']==0


def test_amount_currency_gap_is_distinguished_when_reference_overlaps(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    ledger.import_page('qonto',TransactionPage([
        BankTransaction('a','2026-08-20T10:00:00+00:00',99,'USD','one',external_transaction_id='b1',reference='BANK REF',status='COMPLETED')
    ],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db,amount='100',currency='EUR',reference='BANK REF')
    evidence=local_exact_match_gap_evidence(path)
    assert evidence['diagnosis']=='AMOUNT_CURRENCY_DOMAIN_GAP'
    assert evidence['counts']['reference_overlap_payouts']==1
    assert evidence['counts']['amount_currency_overlap_payouts']==0
    assert evidence['counts']['exact_triplet_overlap_payouts']==0


def test_qonto_completed_is_eligible_but_other_provider_completed_is_not(tmp_path):
    path=tmp_path/'db'; ledger=BankLedger(path)
    tx=BankTransaction('a','2026-08-20T10:00:00+00:00',100,'EUR','one',external_transaction_id='b1',reference='BANK REF',status='COMPLETED')
    ledger.import_page('qonto',TransactionPage([tx],None)); ledger.import_page('otherbank',TransactionPage([tx],None))
    with sqlite3.connect(path) as db: _sumup_schema(db); _payout(db)
    qonto=local_exact_match_gap_evidence(path,bank_provider='qonto')
    other=local_exact_match_gap_evidence(path,bank_provider='otherbank')
    assert qonto['counts']['eligible_credits_total']==1 and qonto['eligible_statuses']==['BOOKED','COMPLETED']
    assert other['counts']['eligible_credits_total']==0 and other['eligible_statuses']==['BOOKED']


def test_missing_ledger_fails_closed_without_creating_database(tmp_path):
    path=tmp_path/'missing.db'
    evidence=local_exact_match_gap_evidence(path)
    assert evidence['status']=='UNMEASURABLE' and evidence['counts'] is None
    assert evidence['provider_exhaustiveness_inferred'] is False and not path.exists()
