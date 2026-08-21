import sqlite3
from dr_cloud_sync.bank_transaction_shape_evidence import qonto_local_transaction_shape


def _db(path):
    db=sqlite3.connect(path)
    db.execute("CREATE TABLE bank_transactions(transaction_id TEXT PRIMARY KEY,provider TEXT NOT NULL,direction TEXT NOT NULL,status TEXT NOT NULL,reference TEXT)")
    return db


def test_missing_database_is_unmeasurable_and_not_created(tmp_path):
    path=tmp_path/'missing.db'
    assert qonto_local_transaction_shape(path)=={'status':'UNMEASURABLE','reason':'LOCAL_DATABASE_MISSING','provider_exhaustiveness_inferred':False}
    assert not path.exists()


def test_completed_credit_status_is_classified_without_emitting_reference(tmp_path):
    path=tmp_path/'db'
    with _db(path) as db:
        db.execute("INSERT INTO bank_transactions VALUES('secret-id','Qonto','CREDIT','COMPLETED','private-reference')")
        db.execute("INSERT INTO bank_transactions VALUES('secret-id-2','Qonto','DEBIT','COMPLETED',NULL)")
    result=qonto_local_transaction_shape(path)
    assert result['cause']=='QONTO_LOCAL_CREDITS_USE_COMPLETED_STATUS'
    assert result['transactions_total']==2
    assert result['direction_counts']=={'CREDIT':1,'DEBIT':1,'OTHER':0}
    assert result['status_counts']=={'COMPLETED':2}
    assert result['credits']=={'total':1,'booked':0,'completed':1,'with_reference':1,'reference_coverage_ratio':1.0}
    assert result['provider_exhaustiveness_inferred'] is False
    assert 'secret-id' not in str(result) and 'private-reference' not in str(result)


def test_booked_credit_remains_distinct(tmp_path):
    path=tmp_path/'db'
    with _db(path) as db:
        db.execute("INSERT INTO bank_transactions VALUES('a','qonto','CREDIT','BOOKED','r')")
    result=qonto_local_transaction_shape(path)
    assert result['cause']=='QONTO_LOCAL_BOOKED_CREDITS_PRESENT'
    assert result['credits']['booked']==1


def test_unknown_status_is_bounded_to_other(tmp_path):
    path=tmp_path/'db'
    with _db(path) as db:
        db.execute("INSERT INTO bank_transactions VALUES('a','Qonto','CREDIT','provider-free-form-state','r')")
    result=qonto_local_transaction_shape(path)
    assert result['status_counts']=={'OTHER':1}
    assert 'provider-free-form-state' not in str(result)


def test_other_provider_rows_are_excluded(tmp_path):
    path=tmp_path/'db'
    with _db(path) as db:
        db.execute("INSERT INTO bank_transactions VALUES('a','OtherBank','CREDIT','BOOKED','r')")
    result=qonto_local_transaction_shape(path)
    assert result['cause']=='NO_LOCAL_QONTO_TRANSACTIONS'
    assert result['transactions_total']==0
