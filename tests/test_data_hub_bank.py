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
