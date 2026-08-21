from datetime import datetime, timezone
import sqlite3

from dr_cloud_sync.bank_source_evidence import qonto_local_source_evidence


def _schema(db):
    db.executescript("""
    CREATE TABLE data_sources(source_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,enabled INTEGER NOT NULL,last_attempt_at TEXT,last_success_at TEXT,last_error TEXT,cursor TEXT,capabilities_json TEXT NOT NULL,stale_after_seconds INTEGER NOT NULL,rows_imported INTEGER NOT NULL DEFAULT 0,last_rows_imported INTEGER,last_run_id INTEGER,data_min_at TEXT,data_max_at TEXT,records_available INTEGER);
    CREATE TABLE connector_diagnostics(diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT NOT NULL,provider TEXT NOT NULL,job_id TEXT,run_id INTEGER,operation TEXT NOT NULL,stage TEXT NOT NULL,endpoint_path TEXT,http_status INTEGER,category TEXT NOT NULL,message TEXT NOT NULL,response_excerpt TEXT,exception_type TEXT,attempt INTEGER NOT NULL,occurred_at TEXT NOT NULL,duration_ms INTEGER,cursor TEXT,request_id TEXT,next_retry_at TEXT,success INTEGER NOT NULL DEFAULT 0);
    """)


def _source(db, *, status='CONNECTED', last_success=None, last_run_id=None, records=None, rows=0, cursor=None):
    db.execute("INSERT INTO data_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
        'bank','BANK','Qonto',status,1,last_success,last_success,None,cursor,'[]',3600,rows,
        0 if last_run_id is not None else None,last_run_id,None,None,records,
    ))


def test_missing_database_is_unmeasurable_and_not_created(tmp_path):
    path=tmp_path/'missing.db'
    result=qonto_local_source_evidence(path)
    assert result=={'status':'UNMEASURABLE','reason':'LOCAL_DATABASE_MISSING','provider_exhaustiveness_inferred':False}
    assert not path.exists()


def test_no_successful_import_is_explicit_without_cursor_value(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,cursor='opaque-secret-cursor')
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_NO_SUCCESSFUL_IMPORT_RUN'
    assert result['source']['freshness']=='CONNECTED_NO_DATA'
    assert result['source']['cursor_present'] is True
    assert 'opaque-secret-cursor' not in str(result)
    assert result['provider_exhaustiveness_inferred'] is False


def test_current_waf_error_is_sanitized_and_classified(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,status='ERROR')
        db.execute("INSERT INTO connector_diagnostics(source_id,provider,operation,stage,http_status,category,message,response_excerpt,exception_type,attempt,occurred_at,success) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ('bank','Qonto','BANK_SYNC','edge_protection',403,'WAF','secret message','sensitive body','HTTPError',1,'2026-08-21T12:00:00+00:00',0))
    result=qonto_local_source_evidence(path)
    assert result['cause']=='QONTO_SYNC_BLOCKED_WAF'
    assert result['source']['freshness']=='ERROR'
    assert result['latest_diagnostic']=={'category':'WAF','stage':'edge_protection','http_status':403,'success':False,'occurred_at':'2026-08-21T12:00:00+00:00'}
    assert 'secret message' not in str(result) and 'sensitive body' not in str(result)


def test_successful_zero_record_import_is_local_fact_not_provider_exhaustiveness(tmp_path):
    path=tmp_path/'db'
    stamp='2026-08-21T12:00:00+00:00'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,last_success=stamp,last_run_id=4,records=0,rows=0)
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,12,30,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_LOCAL_IMPORT_PROVED_ZERO_RECORDS'
    assert result['source']['freshness']=='FRESH' and result['source']['records_available']==0
    assert result['provider_exhaustiveness_inferred'] is False


def test_stale_local_records_remain_distinct_from_provider_authority(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,last_success='2026-08-21T10:00:00+00:00',last_run_id=7,records=12,rows=12)
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,12,0,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_LOCAL_RECORDS_AVAILABLE'
    assert result['source']['freshness']=='STALE'
    assert result['source']['records_available']==12
    assert result['evidence_scope']=='LOCAL_SYNC_CONTROL_PLANE_ONLY'
