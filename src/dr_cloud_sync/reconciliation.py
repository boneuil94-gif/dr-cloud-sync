"""Conservative deterministic reconciliation; ambiguous matches stay human work."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3, uuid
from pathlib import Path
SCHEMA="""CREATE TABLE IF NOT EXISTS reconciliation_matches(match_id TEXT PRIMARY KEY,left_type TEXT NOT NULL,left_id TEXT NOT NULL,right_type TEXT NOT NULL,right_id TEXT NOT NULL,match_type TEXT NOT NULL,confidence TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,matched_by TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(left_type,left_id,right_type,right_id));"""
class ReconciliationService:
 def __init__(self,path:Path): self.db=sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.executescript(SCHEMA);self.db.commit()
 def reconcile_sales_bank(self):
  sales=self.db.execute("SELECT sale_event_id,raw_reference,line_total_ttc,currency,sold_at FROM sale_events WHERE event_kind='SALE' AND line_total_ttc IS NOT NULL").fetchall();banks=self.db.execute("SELECT transaction_id,reference,amount,currency,booked_at,category FROM bank_transactions WHERE direction='CREDIT'").fetchall();created=0
  for sale in sales:
   candidates=[b for b in banks if b['currency']==sale['currency'] and Decimal(b['amount'])==Decimal(sale['line_total_ttc']) and abs((datetime.fromisoformat(b['booked_at'])-datetime.fromisoformat(sale['sold_at'])).days)<=7]
   exact=[b for b in candidates if sale['raw_reference'] and b['reference']==sale['raw_reference']]
   if len(exact)==1: status,confidence,reason,target='MATCHED','1.0','exact reference, amount and date',exact[0]
   elif len(candidates)==1: status,confidence,reason,target='POSSIBLE','0.7','exact amount and coherent date; human validation required',candidates[0]
   elif len(candidates)>1: status,confidence,reason,target='CONFLICT','0.0','multiple deterministic candidates',candidates[0]
   else: continue
   stamp=datetime.now(timezone.utc).isoformat()
   with self.db: created+=self.db.execute("INSERT OR IGNORE INTO reconciliation_matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(f'match:{uuid.uuid4()}','SALE',sale['sale_event_id'],'BANK_TRANSACTION',target['transaction_id'],'SALE_SETTLEMENT',confidence,reason,status,'SYSTEM' if status=='MATCHED' else 'PROPOSED',stamp,stamp)).rowcount
  return {'created':created,'matches':self.list()}
 def reconcile_sales_sumup(self):
  """Match payments without turning them into Sale events or revenue."""
  sales=self.db.execute("SELECT sale_event_id,raw_reference,line_total_ttc,currency,sold_at FROM sale_events WHERE event_kind='SALE' AND line_total_ttc IS NOT NULL").fetchall()
  payments=self.db.execute("SELECT sumup_transaction_id,transaction_code,client_transaction_id,amount,currency,timestamp FROM sumup_transactions WHERE upper(coalesce(status,'')) NOT IN ('FAILED','CANCELLED')").fetchall();created=0
  for sale in sales:
   candidates=[p for p in payments if p['currency']==sale['currency'] and Decimal(p['amount'])==Decimal(sale['line_total_ttc']) and abs((datetime.fromisoformat(p['timestamp'].replace('Z','+00:00'))-datetime.fromisoformat(sale['sold_at'].replace('Z','+00:00'))).total_seconds())<=86400]
   exact=[p for p in candidates if sale['raw_reference'] and sale['raw_reference'] in {p['transaction_code'],p['client_transaction_id']}]
   target=exact[0] if len(exact)==1 else candidates[0] if len(candidates)==1 else None
   if not target: continue
   status='MATCHED' if len(exact)==1 else 'POSSIBLE';stamp=datetime.now(timezone.utc).isoformat()
   with self.db: created+=self.db.execute("INSERT OR IGNORE INTO reconciliation_matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(f'match:{uuid.uuid4()}','SALE',sale['sale_event_id'],'SUMUP_TRANSACTION',target['sumup_transaction_id'],'SALE_PAYMENT','1.0' if status=='MATCHED' else '0.7','reference, amount and time' if status=='MATCHED' else 'amount and time; validation required',status,'SYSTEM' if status=='MATCHED' else 'PROPOSED',stamp,stamp)).rowcount
  return {'created':created,'matches':self.list()}
 def list(self): return [dict(r) for r in self.db.execute('SELECT * FROM reconciliation_matches ORDER BY created_at DESC')]
