from datetime import datetime, timezone
import json
import sqlite3

from dr_cloud_sync.bank_source_evidence import qonto_local_source_evidence


def _schema(db):
    db.executescript("""
    CREATE TABLE data_sources(source_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,enabled INTEGER NOT NULL,last_attempt_at TEXT,last_success_at TEXT,last_error TEXT,cursor TEXT,capabilities_json TEXT NOT NULL,stale_after_seconds INTEGER NOT NULL,rows_imported INTEGER NOT NULL DEFAULT 0,last_rows_imported INTEGER,last_run_id INTEGER,data_min_at TEXT,data_max_at TEXT,records_available INTEGER);
    CREATE TABLE sync_jobs(job_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,job_type TEXT NOT NULL,interval_seconds INTEGER NOT NULL,dependencies_json TEXT NOT NULL,max_attempts INTEGER NOT NULL,next_run_at TEXT,last_run_at TEXT,status TEXT NOT NULL DEFAULT 'PENDING',attempts INTEGER NOT NULL DEFAULT 0,duration_ms INTEGER,error TEXT,lock_token TEXT,locked_at TEXT);
    CREATE TABLE data_hub_sync_runs(run_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,attempt INTEGER NOT NULL,result_json TEXT,error TEXT);
    CREATE TABLE connector_diagnostics(diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT NOT NULL,provider TEXT NOT NULL,job_id TEXT,run_id INTEGER,operation TEXT NOT NULL,stage TEXT NOT NULL,endpoint_path TEXT,http_status INTEGER,category TEXT NOT NULL,message TEXT NOT NULL,response_excerpt TEXT,exception_type TEXT,attempt INTEGER NOT NULL,occurred_at TEXT NOT NULL,duration_ms INTEGER,cursor TEXT,request_id TEXT,next_retry_at TEXT,success INTEGER NOT NULL DEFAULT 0);
    """)
    db.execute("INSERT INTO sync_jobs(job_id,source_id,job_type,interval_seconds,dependencies_json,max_attempts,status) VALUES('sync_bank','bank','BANK',60,'[]',3,'SUCCEEDED')")
    db.execute("INSERT INTO sync_jobs(job_id,source_id,job_type,interval_seconds,dependencies_json,max_attempts,status) VALUES('refresh_finance','bank','REFRESH_FINANCE',60,'[]',3,'SUCCEEDED')")


def _source(db, *, status='CONNECTED', last_success=None, last_run_id=None, records=None, rows=0, cursor=None):
    db.execute("INSERT INTO data_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
        'bank','BANK','Qonto',status,1,last_success,last_success,None,cursor,'[]',3600,rows,
        0 if last_run_id is not None else None,last_run_id,None,None,records,
    ))


def _run(db, job_id, *, completed='2026-08-21T12:00:00+00:00', result=None):
    db.execute("INSERT INTO data_hub_sync_runs(job_id,started_at,completed_at,status,attempt,result_json) VALUES(?,?,?,?,?,?)",
               (job_id,completed,completed,'SUCCEEDED',1,json.dumps(result or {})))


def test_missing_database_is_unmeasurable_and_not_created(tmp_path):
    path=tmp_path/'missing.db'
    result=qonto_local_source_evidence(path)
    assert result=={'status':'UNMEASURABLE','reason':'LOCAL_DATABASE_MISSING','provider_exhaustiveness_inferred':False}
    assert not path.exists()


def test_no_successful_import_is_explicit_and_omits_cursor(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,cursor='opaque-secret-cursor')
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_NO_SUCCESSFUL_IMPORT_RUN'
    assert result['source']['freshness']=='CONNECTED_NO_DATA'
    assert result['source']['successful_import_run_present'] is False
    assert 'cursor' not in str(result).lower()
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
        _schema(db); _source(db,last_success=stamp,last_run_id=4,records=999,rows=5)
        _run(db,'sync_bank',completed=stamp,result={'rows_imported':0,'records_available':0,'data_min_at':None,'data_max_at':None})
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,12,30,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_LOCAL_IMPORT_PROVED_ZERO_RECORDS'
    assert result['source']['freshness']=='FRESH' and result['source']['records_available']==0
    assert result['source']['last_rows_imported']==0
    assert result['provider_exhaustiveness_inferred'] is False


def test_later_non_bank_job_cannot_overwrite_bank_import_evidence(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,last_success='2026-08-21T12:30:00+00:00',last_run_id=9,records=42,rows=42)
        _run(db,'sync_bank',completed='2026-08-21T12:00:00+00:00',result={'rows_imported':0,'records_available':0})
        _run(db,'refresh_finance',completed='2026-08-21T12:30:00+00:00',result={'rows_imported':42,'records_available':42})
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,12,40,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_LOCAL_IMPORT_PROVED_ZERO_RECORDS'
    assert result['source']['records_available']==0 and result['source']['last_rows_imported']==0
    assert result['source']['last_import_completed_at']=='2026-08-21T12:00:00+00:00'


def test_stale_local_records_remain_distinct_from_provider_authority(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db); _source(db,last_success='2026-08-21T10:00:00+00:00',last_run_id=7,records=12,rows=12)
        _run(db,'sync_bank',completed='2026-08-21T10:00:00+00:00',result={'rows_imported':12,'records_available':12})
    result=qonto_local_source_evidence(path,now=datetime(2026,8,21,12,0,tzinfo=timezone.utc))
    assert result['cause']=='QONTO_LOCAL_RECORDS_AVAILABLE'
    assert result['source']['freshness']=='STALE'
    assert result['source']['records_available']==12
    assert result['evidence_scope']=='LOCAL_SYNC_CONTROL_PLANE_ONLY'


def test_missing_qonto_source_is_measurable_with_explicit_scope(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        _schema(db)
    result=qonto_local_source_evidence(path)
    assert result['status']=='MEASURABLE'
    assert result['cause']=='QONTO_SOURCE_STATE_MISSING'
    assert result['evidence_scope']=='LOCAL_SYNC_CONTROL_PLANE_ONLY'
    assert result['provider_exhaustiveness_inferred'] is False
