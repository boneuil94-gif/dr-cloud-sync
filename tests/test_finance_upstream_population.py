import json
import sqlite3

from dr_cloud_sync.finance_upstream_population import upstream_payment_population


def _db(path):
    db=sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE sales(sale_id TEXT PRIMARY KEY,source TEXT NOT NULL);
    CREATE TABLE sale_payments(payment_id TEXT PRIMARY KEY,sale_id TEXT NOT NULL,canonical_payment_type TEXT,quality_status TEXT);
    CREATE TABLE sales_sync_states(source TEXT PRIMARY KEY,last_report_json TEXT);
    """)
    return db


def test_missing_ledger_is_unmeasurable(tmp_path):
    evidence=upstream_payment_population(tmp_path/'missing.db')
    assert evidence['evidence_status']=='UNMEASURABLE'
    assert evidence['reason']=='REQUIRED_LEDGER_MISSING'
    assert evidence['counts'] is None


def test_incomplete_required_schema_fails_closed(tmp_path):
    path=tmp_path/'broken.db'
    db=sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE sales(sale_id TEXT PRIMARY KEY,source TEXT NOT NULL);
    CREATE TABLE sale_payments(payment_id TEXT PRIMARY KEY,sale_id TEXT NOT NULL,canonical_payment_type TEXT);
    CREATE TABLE sales_sync_states(source TEXT PRIMARY KEY,last_report_json TEXT);
    """)
    db.close()
    evidence=upstream_payment_population(path)
    assert evidence['evidence_status']=='UNMEASURABLE'
    assert evidence['reason']=='REQUIRED_SCHEMA_INCOMPLETE'


def test_zero_payments_preserves_bounded_api_exposure_diagnostic(tmp_path):
    path=tmp_path/'empty-payments.db'; db=_db(path)
    db.execute("INSERT INTO sales VALUES('s1','SHOPCAISSE')")
    db.execute("INSERT INTO sales_sync_states VALUES('SHOPCAISSE',?)",(json.dumps({
        'shopcaisse_payments':'API_NOT_EXPOSED','tickets_observed':12,
        'payment_objects_observed':0,'some_provider_field':'must-not-appear'}),))
    db.commit(); db.close()
    evidence=upstream_payment_population(path)
    assert evidence['evidence_status']=='MEASURABLE'
    assert evidence['counts']['shopcaisse_sales']==1
    assert evidence['counts']['shopcaisse_payments']==0
    assert evidence['counts']['shopcaisse_payments_card_and_valid']==0
    assert evidence['diagnostics']=={
        'shopcaisse_payment_exposure':'API_NOT_EXPOSED',
        'tickets_observed_presence':'NONZERO',
        'payment_objects_observed_presence':'ZERO',
    }
    assert 'some_provider_field' not in str(evidence)


def test_population_buckets_are_bounded_and_exclude_other_sales_sources(tmp_path):
    path=tmp_path/'population.db'; db=_db(path)
    db.executemany("INSERT INTO sales VALUES(?,?)",[
        ('s1','SHOPCAISSE'),('s2','SHOPCAISSE'),('p1','PRESTASHOP')])
    db.executemany("INSERT INTO sale_payments VALUES(?,?,?,?)",[
        ('a','s1','CARD','VALID'),
        ('b','s1','CARD','INVALID'),
        ('c','s2','CASH','VALID'),
        ('d','s2','UNKNOWN','UNSUPPORTED'),
        ('e','p1','CARD','VALID'),
    ])
    db.execute("INSERT INTO sales_sync_states VALUES('SHOPCAISSE',?)",(json.dumps({
        'shopcaisse_payments':'EXPOSED','tickets_observed':2,'payment_objects_observed':4}),))
    db.commit(); db.close()
    evidence=upstream_payment_population(path)
    assert evidence['counts']=={
        'shopcaisse_sales':2,
        'shopcaisse_sales_with_any_payment':2,
        'shopcaisse_payments':4,
        'shopcaisse_payments_card':2,
        'shopcaisse_payments_non_card_known':1,
        'shopcaisse_payments_unknown_or_missing_type':1,
        'shopcaisse_payments_quality_valid':2,
        'shopcaisse_payments_quality_non_valid':2,
        'shopcaisse_payments_card_and_valid':1,
        'shopcaisse_payments_card_and_non_valid':1,
    }
    assert evidence['diagnostics']['shopcaisse_payment_exposure']=='EXPOSED'
    assert evidence['provider_exhaustiveness_inferred'] is False
    assert evidence['safety']['database_read_only'] is True
    assert evidence['safety']['provider_network_calls'] is False


def test_population_preserves_exact_settlement_casing(tmp_path):
    path=tmp_path/'exact-casing.db'; db=_db(path)
    db.execute("INSERT INTO sales VALUES('s1','SHOPCAISSE')")
    db.executemany("INSERT INTO sale_payments VALUES(?,?,?,?)",[
        ('a','s1','CARD','valid'),
        ('b','s1','card','VALID'),
        ('c','s1','CARD','VALID'),
    ])
    db.commit(); db.close()
    counts=upstream_payment_population(path)['counts']
    assert counts['shopcaisse_payments']==3
    assert counts['shopcaisse_payments_card']==2
    assert counts['shopcaisse_payments_unknown_or_missing_type']==1
    assert counts['shopcaisse_payments_quality_valid']==2
    assert counts['shopcaisse_payments_quality_non_valid']==1
    assert counts['shopcaisse_payments_card_and_valid']==1
    assert counts['shopcaisse_payments_card_and_non_valid']==1


def test_invalid_or_unrecognised_control_plane_report_stays_unknown(tmp_path):
    path=tmp_path/'diagnostic.db'; db=_db(path)
    db.execute("INSERT INTO sales_sync_states VALUES('SHOPCAISSE','not-json')")
    db.commit(); db.close()
    evidence=upstream_payment_population(path)
    assert evidence['evidence_status']=='MEASURABLE'
    assert evidence['diagnostics']=={
        'shopcaisse_payment_exposure':'UNKNOWN',
        'tickets_observed_presence':'UNKNOWN',
        'payment_objects_observed_presence':'UNKNOWN',
    }


def test_evidence_shape_never_emits_raw_payment_or_business_values(tmp_path):
    path=tmp_path/'safe.db'; db=_db(path)
    db.execute("INSERT INTO sales VALUES('secret-sale','SHOPCAISSE')")
    db.execute("INSERT INTO sale_payments VALUES('secret-payment','secret-sale','CARD','VALID')")
    db.execute("INSERT INTO sales_sync_states VALUES('SHOPCAISSE',?)",(json.dumps({
        'shopcaisse_payments':'EXPOSED','tickets_observed':1,'payment_objects_observed':1,
        'raw_label':'private-card-label'}),))
    db.commit(); db.close()
    evidence=upstream_payment_population(path)
    encoded=json.dumps(evidence)
    for forbidden in ('secret-sale','secret-payment','private-card-label'):
        assert forbidden not in encoded
    assert evidence['safety']=={
        'database_read_only':True,
        'provider_network_calls':False,
        'external_provider_auth':'NONE',
        'mutations':False,
        'provider_values_emitted':False,
        'row_level_ids_emitted':False,
        'sensitive_values_emitted':False,
    }
