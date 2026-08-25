import sqlite3

from dr_cloud_sync.finance_upstream_funnel import upstream_settlement_funnel


def _schema(db):
    db.executescript("""
    CREATE TABLE sales(sale_id TEXT PRIMARY KEY,source TEXT NOT NULL);
    CREATE TABLE sale_payments(payment_id TEXT PRIMARY KEY,sale_id TEXT NOT NULL,canonical_payment_type TEXT NOT NULL,quality_status TEXT NOT NULL);
    CREATE TABLE payment_settlement_links(source_type TEXT NOT NULL,source_id TEXT NOT NULL,target_type TEXT,target_id TEXT,status TEXT NOT NULL);
    CREATE TABLE sumup_transactions(sumup_transaction_id TEXT PRIMARY KEY);
    CREATE TABLE sumup_payouts(payout_id TEXT PRIMARY KEY);
    CREATE TABLE sumup_payout_items(item_id TEXT PRIMARY KEY,payout_id TEXT NOT NULL,sumup_transaction_id TEXT);
    """)


def test_upstream_funnel_counts_only_durable_exact_links(tmp_path):
    path=tmp_path/'ledger.db'
    with sqlite3.connect(path) as db:
        _schema(db)
        db.executemany("INSERT INTO sales VALUES(?,?)", [('s1','SHOPCAISSE'),('s2','SHOPCAISSE'),('s3','OTHER')])
        db.executemany("INSERT INTO sale_payments VALUES(?,?,?,?)", [
            ('p1','s1','CARD','VALID'),('p2','s1','CARD','VALID'),('p3','s2','CARD','VALID'),
            ('cash','s2','CASH','VALID'),('bad','s2','CARD','INVALID'),('other','s3','CARD','VALID')])
        db.executemany("INSERT INTO sumup_transactions VALUES(?)", [('t1',),('t2',),('t3',),('t4',)])
        db.executemany("INSERT INTO payment_settlement_links VALUES(?,?,?,?,?)", [
            ('SHOPCAISSE_PAYMENT','p1','SUMUP_TRANSACTION','t1','MATCHED'),
            ('SHOPCAISSE_PAYMENT','p2','SUMUP_TRANSACTION','t2','MATCHED'),
            ('SHOPCAISSE_PAYMENT','p2','SUMUP_TRANSACTION','t3','MATCHED'),
            ('SHOPCAISSE_PAYMENT','p3','SUMUP_TRANSACTION','missing','MATCHED'),
            ('SHOPCAISSE_PAYMENT','p3','SUMUP_TRANSACTION','t4','UNMATCHED')])
        db.executemany("INSERT INTO sumup_payouts VALUES(?)", [('po1',),('po2',)])
        db.executemany("INSERT INTO sumup_payout_items VALUES(?,?,?)", [
            ('i1','po1','t1'),('i2','po1','t2'),('i3','po2','t2')])
    result=upstream_settlement_funnel(path)
    assert result['evidence_status']=='MEASURABLE'
    assert result['counts']=={
        'shopcaisse_sales_with_eligible_card_payment':2,
        'eligible_card_payments':3,
        'payments_without_matched_sumup_transaction':1,
        'payments_with_unique_matched_sumup_transaction':1,
        'payments_with_multiple_matched_sumup_transactions':1,
        'unique_transaction_payments_without_payout_membership':0,
        'unique_transaction_payments_with_unique_payout_membership':1,
        'unique_transaction_payments_with_multiple_payout_memberships':0,
    }
    assert result['coverage']['payment_to_unique_sumup_transaction_ratio']==1/3
    assert result['coverage']['unique_transaction_to_unique_payout_ratio']==1
    assert result['coverage']['sale_to_qonto_coverage_claimed'] is False
    assert result['provider_exhaustiveness_inferred'] is False
    assert result['safety']['database_read_only'] is True
    assert 'p1' not in str(result) and 't1' not in str(result) and 'po1' not in str(result)


def test_upstream_funnel_keeps_multiple_payout_membership_ambiguous(tmp_path):
    path=tmp_path/'ledger.db'
    with sqlite3.connect(path) as db:
        _schema(db)
        db.execute("INSERT INTO sales VALUES('s','SHOPCAISSE')")
        db.execute("INSERT INTO sale_payments VALUES('p','s','CARD','VALID')")
        db.execute("INSERT INTO sumup_transactions VALUES('t')")
        db.execute("INSERT INTO payment_settlement_links VALUES('SHOPCAISSE_PAYMENT','p','SUMUP_TRANSACTION','t','MATCHED')")
        db.executemany("INSERT INTO sumup_payouts VALUES(?)", [('a',),('b',)])
        db.executemany("INSERT INTO sumup_payout_items VALUES(?,?,?)", [('i1','a','t'),('i2','b','t')])
    counts=upstream_settlement_funnel(path)['counts']
    assert counts['unique_transaction_payments_with_unique_payout_membership']==0
    assert counts['unique_transaction_payments_with_multiple_payout_memberships']==1


def test_upstream_funnel_fails_closed_when_required_ledger_missing(tmp_path):
    missing=upstream_settlement_funnel(tmp_path/'missing.db')
    assert missing['evidence_status']=='UNMEASURABLE'
    assert missing['counts'] is None
    path=tmp_path/'partial.db'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE sales(sale_id TEXT PRIMARY KEY,source TEXT)')
    partial=upstream_settlement_funnel(path)
    assert partial['evidence_status']=='UNMEASURABLE'
    assert partial['reason']=='REQUIRED_LEDGER_MISSING'


def test_upstream_funnel_fails_closed_when_required_column_missing(tmp_path):
    path=tmp_path/'partial-schema.db'
    with sqlite3.connect(path) as db:
        _schema(db)
        db.execute('ALTER TABLE sale_payments RENAME TO sale_payments_full')
        db.execute('CREATE TABLE sale_payments(payment_id TEXT PRIMARY KEY,sale_id TEXT NOT NULL,canonical_payment_type TEXT NOT NULL)')
    result=upstream_settlement_funnel(path)
    assert result['evidence_status']=='UNMEASURABLE'
    assert result['reason']=='REQUIRED_SCHEMA_INCOMPLETE'
    assert result['counts'] is None
