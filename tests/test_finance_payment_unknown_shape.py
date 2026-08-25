import sqlite3

from dr_cloud_sync.finance_payment_unknown_shape import upstream_unknown_payment_signal_shape


def _db(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE sales(sale_id TEXT PRIMARY KEY, source TEXT NOT NULL);
        CREATE TABLE sale_payments(
          payment_id TEXT PRIMARY KEY, sale_id TEXT NOT NULL,
          payment_type TEXT, name TEXT, description TEXT,
          canonical_payment_type TEXT, mapping_rule TEXT, mapping_version TEXT
        );
        """)
        for index in range(1, 6):
            db.execute("INSERT INTO sales VALUES(?, 'SHOPCAISSE')", (f's{index}',))
        rows = [
            ('p1','s1','Alpha Tender',' alpha   tender ',None,'UNKNOWN','unknown-label','shopcaisse-payment-types-v2'),
            ('p2','s2','Alpha Tender','Alpha Tender',None,'UNKNOWN','unknown-label','shopcaisse-payment-types-v2'),
            ('p3','s3','Alpha Tender','Alpha Tender',None,'UNKNOWN','unknown-label','shopcaisse-payment-types-v2'),
            ('p4','s4','Beta Tender','Other Name',None,'UNKNOWN','unknown-label','shopcaisse-payment-types-v2'),
            ('p5','s5','Known','Known',None,'CASH','exact-normalized:payment_type:cash','shopcaisse-payment-types-v2'),
        ]
        db.executemany("INSERT INTO sale_payments VALUES(?,?,?,?,?,?,?,?)", rows)


def test_unknown_shape_is_aggregate_only_and_current_unknown_only(tmp_path):
    path = tmp_path/'ledger.db'; _db(path)
    result = upstream_unknown_payment_signal_shape(path)
    assert result['evidence_status'] == 'MEASURABLE'
    assert result['provider_exhaustiveness_inferred'] is False
    assert result['counts'] == {
        'unknown_current_mapping_rows': 4,
        'unknown_payment_type_name_equal': 3,
        'unknown_payment_type_name_different': 1,
        'unknown_description_present': 0,
        'unknown_distinct_payment_type_signatures': 2,
        'unknown_distinct_name_signatures': 2,
        'unknown_distinct_pair_signatures': 2,
        'unknown_largest_payment_type_bucket': 3,
        'unknown_largest_name_bucket': 3,
        'unknown_largest_pair_bucket': 3,
        'unknown_rows_outside_largest_pair_bucket': 1,
    }
    assert result['safety']['database_read_only'] is True
    assert result['safety']['raw_payment_values_emitted'] is False
    assert 'Alpha Tender' not in str(result) and 'Beta Tender' not in str(result)


def test_unknown_shape_excludes_legacy_mapping_rows(tmp_path):
    path = tmp_path/'legacy.db'; _db(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sales VALUES('s6','SHOPCAISSE')")
        db.execute("INSERT INTO sale_payments VALUES('p6','s6','Secret Legacy','Secret Legacy',NULL,'UNKNOWN','unknown-label','v1')")
    result = upstream_unknown_payment_signal_shape(path)
    assert result['counts']['unknown_current_mapping_rows'] == 4
    assert 'Secret Legacy' not in str(result)


def test_unknown_shape_fails_closed_on_missing_primary_signal(tmp_path):
    path = tmp_path/'missing.db'; _db(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sales VALUES('s6','SHOPCAISSE')")
        db.execute("INSERT INTO sale_payments VALUES('p6','s6',NULL,NULL,NULL,'UNKNOWN','unknown-label','shopcaisse-payment-types-v2')")
    result = upstream_unknown_payment_signal_shape(path)
    assert result['evidence_status'] == 'UNMEASURABLE'
    assert result['reason'] == 'UNKNOWN_SIGNAL_MISSING'
    assert result['counts'] is None


def test_unknown_shape_fails_closed_on_missing_schema(tmp_path):
    missing = upstream_unknown_payment_signal_shape(tmp_path/'absent.db')
    assert missing['evidence_status'] == 'UNMEASURABLE'
    assert missing['reason'] == 'REQUIRED_LEDGER_MISSING'

    path = tmp_path/'schema.db'
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sales(sale_id TEXT PRIMARY KEY, source TEXT)")
        db.execute("CREATE TABLE sale_payments(sale_id TEXT, payment_type TEXT)")
    incomplete = upstream_unknown_payment_signal_shape(path)
    assert incomplete['evidence_status'] == 'UNMEASURABLE'
    assert incomplete['reason'] == 'REQUIRED_SCHEMA_INCOMPLETE'
