from datetime import datetime,timezone,timedelta
from decimal import Decimal
from dr_cloud_sync.data_hub import DataHub,JobDefinition
from dr_cloud_sync.bank import BankLedger,BankTransaction,BankBalance,TransactionPage

class FakeBank:
 configured=True
 def __init__(self): self.calls=0
 def health(self): return {'status':'CONNECTED'}
 def accounts(self): return ()
 def balances(self): return [BankBalance('a',Decimal('42'),'EUR','2026-01-01T00:00:00+00:00')]
 def transactions(self,cursor=None):
  self.calls+=1
  if cursor is None:return TransactionPage([BankTransaction('a','2026-01-01T00:00:00+00:00',Decimal('12'),'EUR','sale',reference='r')],'next')
  return TransactionPage([BankTransaction('a','2026-01-02T00:00:00+00:00',Decimal('-2'),'EUR','fee',external_transaction_id='fee')],None)

def test_bank_pagination_balance_and_resync_idempotence(tmp_path):
 ledger=BankLedger(tmp_path/'db'); provider=FakeBank(); first=ledger.sync('fake',provider)
 assert first['rows_imported']==2 and provider.calls==2 and ledger.balances()[0]['current_balance']=='42'
 second=ledger.sync('fake',provider);assert second['rows_imported']==0 and len(ledger.transactions())==2

def test_runtime_due_dependency_freshness_and_lock(tmp_path):
 current=[datetime(2026,1,1,tzinfo=timezone.utc)];hub=DataHub(tmp_path/'db',clock=lambda:current[0]);hub.register_source('s','SALES','fake',configured=True,stale_after_seconds=10);hub.register_job(JobDefinition('sync','s','SYNC',5));hub.register_job(JobDefinition('projection','s','PROJECT',5,('sync',)))
 assert hub.run('projection',lambda c:{})['status']=='BLOCKED'
 assert hub.run('sync',lambda c:{'rows_imported':1,'cursor':'c'})['status']=='SUCCEEDED';assert hub.sources()[0]['freshness']=='FRESH'
 current[0]+=timedelta(seconds=11);assert hub.sources()[0]['freshness']=='STALE'

def test_inventory_statuses_disabled_and_next_run_are_explicit(tmp_path):
 hub=DataHub(tmp_path/'states.db')
 hub.register_source('disabled','SOCIAL_ANALYTICS','none',enabled=False)
 hub.register_source('unsupported','PAYMENTS','none',status='UNSUPPORTED')
 hub.register_job(JobDefinition('social_job','disabled','SOCIAL',60))
 rows={row['source_id']:row for row in hub.sources()}
 assert rows['disabled']['freshness']=='DISABLED'
 assert rows['disabled']['next_run_at'] is not None
 assert rows['unsupported']['status']=='UNSUPPORTED'

def test_due_job_without_runtime_handler_is_blocked(tmp_path):
 hub=DataHub(tmp_path/'handlers.db');hub.register_source('s','SALES','real',configured=True);hub.register_job(JobDefinition('orphan','s','MISSING',60))
 result=hub.run_due({})[0]
 assert result['status']=='BLOCKED' and result['error']=='runtime handler missing: MISSING'

def test_worker_heartbeat_proves_shared_database_without_exposing_path(tmp_path):
 hub=DataHub(tmp_path/'runtime.db');hub.heartbeat();runtime=hub.runtime({})
 assert runtime['worker_heartbeats'][0]['database_fingerprint']==runtime['database_fingerprint']
 assert str(tmp_path) not in str(runtime)


def test_connected_configured_without_import_is_connected_no_data(tmp_path):
 hub=DataHub(tmp_path/'connected.db');hub.register_source('s','SALES','fake',configured=True)
 row=hub.sources()[0]
 assert row['freshness']=='CONNECTED_NO_DATA'
 assert row['last_rows_imported'] is None and row['records_available'] is None


def test_health_check_success_without_business_data_is_connected_no_data(tmp_path):
 hub=DataHub(tmp_path/'health.db');hub.register_source('s','SALES','fake',configured=True)
 hub.connector_health_check('s','fake',lambda: None,operation='HEALTH',stage='test',endpoint_path='/health')
 assert hub.sources()[0]['freshness']=='CONNECTED_NO_DATA'


def test_successful_import_persists_last_run_total_coverage_and_known_zero(tmp_path):
 current=[datetime(2026,1,1,tzinfo=timezone.utc)];hub=DataHub(tmp_path/'import.db',clock=lambda:current[0])
 hub.register_source('s','SALES','fake',configured=True);hub.register_job(JobDefinition('sync','s','SYNC',60))
 hub.run('sync',lambda c:{'rows_imported':0,'cursor':'done','data_min_at':'2026-01-01T00:00:00+00:00','data_max_at':'2026-01-31T23:59:59+00:00','records_available':0})
 row=hub.sources()[0]
 assert row['freshness']=='FRESH'
 assert row['last_rows_imported']==0 and row['rows_imported']==0 and row['records_available']==0
 assert row['last_run_id']==1 and row['data_min_at'].startswith('2026-01-01') and row['data_max_at'].startswith('2026-01-31')


def test_unknown_connector_values_remain_null_not_false_zero(tmp_path):
 hub=DataHub(tmp_path/'unknown.db');hub.register_source('s','SALES','fake',configured=True);hub.register_job(JobDefinition('sync','s','SYNC',60))
 hub.run('sync',lambda c:{'cursor':'done'})
 row=hub.sources()[0]
 assert row['last_rows_imported'] is None
 assert row['records_available'] is None and row['data_min_at'] is None and row['data_max_at'] is None


def test_worker_states_missing_stale_and_database_mismatch(tmp_path):
 current=[datetime(2026,1,1,tzinfo=timezone.utc)];hub=DataHub(tmp_path/'worker.db',clock=lambda:current[0])
 assert hub.worker_state()=='MISSING'
 hub.heartbeat(); assert hub.worker_state()=='HEALTHY'
 current[0]+=timedelta(seconds=121); assert hub.worker_state()=='STALE'
 with hub.connect() as db: db.execute("UPDATE automation_worker_heartbeat SET database_fingerprint='other'")
 assert hub.worker_state()=='DATABASE_MISMATCH'


def test_existing_database_migrates_additive_data_source_columns(tmp_path):
 db=tmp_path/'legacy.db'
 import sqlite3
 with sqlite3.connect(db) as conn:
  conn.execute("CREATE TABLE data_sources(source_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,enabled INTEGER NOT NULL,last_attempt_at TEXT,last_success_at TEXT,last_error TEXT,cursor TEXT,capabilities_json TEXT NOT NULL,stale_after_seconds INTEGER NOT NULL,rows_imported INTEGER NOT NULL DEFAULT 0)")
  conn.execute("INSERT INTO data_sources VALUES('s','SALES','fake','CONNECTED',1,NULL,NULL,NULL,NULL,'[]',3600,5)")
 hub=DataHub(db); row=hub.sources()[0]
 assert row['rows_imported']==5 and row['last_rows_imported'] is None and row['records_available'] is None


def test_sources_uses_single_connection_for_sources_and_schedule(tmp_path):
 hub=DataHub(tmp_path/'connections.db');hub.register_source('s','SALES','fake',configured=True);hub.register_job(JobDefinition('sync','s','SYNC',60))
 calls={'count':0}; original=hub.connect
 def counted():
  calls['count']+=1; return original()
 hub.connect=counted
 hub.sources()
 assert calls['count']==1


def test_production_evidence_api_payload_is_read_only_shape(tmp_path):
 hub=DataHub(tmp_path/'evidence.db');hub.register_source('s','SALES','fake',configured=True)
 payload=hub.production_evidence()['sources'][0]
 assert set(payload) == {'provider','source_id','configuration','connectivity','freshness','last_attempt_at','last_success_at','last_rows_imported','rows_imported','data_min_at','data_max_at','records_available','last_error','next_run_at','last_run_id'}
 assert payload['configuration']=='CONFIGURED' and payload['freshness']=='CONNECTED_NO_DATA'
