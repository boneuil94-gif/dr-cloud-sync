"""Explainable, read-only projections over the existing business ledgers.

The projection owns no accounting ledger: sales, bank, purchasing and stock
remain authoritative.  Missing evidence is represented as unavailable, never 0.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib, json, logging, sqlite3
from .schema import ensure_schema

SCHEMA="""
CREATE TABLE IF NOT EXISTS finance_recurring_charges(charge_id TEXT PRIMARY KEY,label TEXT NOT NULL,category TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,frequency TEXT NOT NULL,next_due_at TEXT,status TEXT NOT NULL,vat_amount TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_snapshots(fingerprint TEXT PRIMARY KEY,period_start TEXT NOT NULL,period_end TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
"""

CATEGORIES=("SALES_SETTLEMENT","SUPPLIER_PAYMENT","BANK_FEE","RENT","TAX","PAYROLL","FINANCING","TRANSFER","REFUND","OTHER","UNKNOWN")
log = logging.getLogger(__name__)

def _d(value): return Decimal(str(value or 0))
def _iso(value): return datetime.fromisoformat(value.replace("Z","+00:00"))
def _money(value,available=True,currency="EUR",source="",method=""):
    return {"value":str(value) if available else None,"currency":currency,"available":available,"source":source,"method":method}

class FinanceProjection:
 def __init__(self,bank,sales,purchases=None,purchase_costs=None,sumup_transactions=None,sumup_settlements=None):
  self.bank,self.sales,self.purchases,self.purchase_costs=bank,sales,purchases,purchase_costs; self.sumup_transactions=sumup_transactions;self.sumup_settlements=sumup_settlements;self.db=bank.db
  ensure_schema(self.db,SCHEMA,owner="Finance")
 def _period(self,days,now): return now-timedelta(days=days),now
 def _transactions(self,start,end): return [t for t in self.bank.transactions() if start<=_iso(t["booked_at"])<end]
 def _sales(self,start,end): return self.db.execute("SELECT * FROM sale_events WHERE sold_at>=? AND sold_at<?",(start.isoformat(),end.isoformat())).fetchall()
 def summary(self,days=30,now=None):
  now=now or datetime.now(timezone.utc);start,end=self._period(days,now); tx=self._transactions(start,end); sales=self._sales(start,end)
  effect=lambda r: Decimal(1) if r["event_kind"] in {"SALE","ADJUSTMENT"} else Decimal(-1)
  def complete(field): return bool(sales) and all(r[field] is not None for r in sales)
  ttc=sum((_d(r["line_total_ttc"])*effect(r) for r in sales),Decimal()) if complete("line_total_ttc") else None
  ht=sum((_d(r["line_total_ht"])*effect(r) for r in sales),Decimal()) if complete("line_total_ht") else None
  refunds=sum((_d(r["line_total_ttc"]) for r in sales if r["event_kind"] in {"REFUND","RETURN"} and r["line_total_ttc"] is not None),Decimal())
  credits=sum((_d(t["amount"]) for t in tx if t["direction"]=="CREDIT" and t["category"]!="TRANSFER"),Decimal())
  debits=-sum((_d(t["amount"]) for t in tx if t["direction"]=="DEBIT" and t["category"]!="TRANSFER"),Decimal())
  sales_fresh=self.sales.status(as_of=now); balances=self.bank.balances(); bank_latest=max((b["imported_at"] for b in balances),default=None)
  method="sum Sales Ledger events; SALE/ADJUSTMENT positive, REFUND/RETURN/CANCELLATION negative"
  revenue={"ttc":_money(ttc,ttc is not None,source="Sales Ledger",method=method),"ht":_money(ht,ht is not None,source="Sales Ledger",method=method),"refunds":_money(refunds,source="Sales Ledger",method="refund and return events only"),"events":len(sales)}
  tax_available=ttc is not None and ht is not None
  collected=(ttc-ht) if tax_available else None
  tax={"collected":_money(collected,tax_available,source="Sales Ledger",method="reliable TTC - reliable HT"),"deductible":_money(0,False,source="supplier invoices",method="unavailable without reliable invoice VAT"),"position":_money(0,False,source="finance projection",method="collected - deductible; ESTIMATION"),"label":"ESTIMATION","unavailable_sales":sum(r["line_total_ht"] is None or r["line_total_ttc"] is None for r in sales)}
  by_category={c:str(sum((_d(t["amount"]) for t in tx if t["category"]==c),Decimal())) for c in CATEGORIES}
  cash={"inflows":_money(credits,source="Bank Ledger",method="credits excluding internal transfers"),"outflows":_money(debits,source="Bank Ledger",method="absolute debits excluding internal transfers"),"net":_money(credits-debits,source="Bank Ledger",method="inflows - outflows"),"categories":by_category,"matched":sum(t["category_state"]=="CONFIRMED" for t in tx),"unmatched":sum(t["category_state"]!="CONFIRMED" for t in tx)}
  cost_rows=[r for r in sales if r["event_kind"]=="SALE" and r["line_total_ttc"] is not None];covered=[r for r in cost_rows if r["cost_basis"] is not None]
  covered_revenue=sum((_d(r["line_total_ttc"]) for r in covered),Decimal());cost=sum((_d(r["cost_basis"])*_d(r["quantity"]) for r in covered),Decimal())
  total_revenue=sum((_d(r["line_total_ttc"]) for r in cost_rows),Decimal()); coverage=(covered_revenue/total_revenue*100 if total_revenue else Decimal())
  profitability={"covered_revenue":_money(covered_revenue,source="Sales Ledger",method="sales with historical cost_basis"),"uncovered_revenue":_money(total_revenue-covered_revenue,source="Sales Ledger"),"gross_margin":_money(covered_revenue-cost,bool(covered),source="Sales Ledger",method="covered revenue - attributed historical cost"),"coverage_percent":str(coverage),"available":bool(covered)}
  result={"period":{"start":start.isoformat(),"end":end.isoformat(),"days":days},"currency":"EUR","revenue":revenue,"cashflow":cash,"tax":tax,"profitability":profitability,"balances":balances,"current_balance":_money(sum((_d(b["current_balance"]) for b in balances),Decimal()),bool(balances),source="Bank Ledger/Qonto",method="sum latest account balances"),"freshness":{"sales":sales_fresh["freshness"],"sales_observed_at":sales_fresh["last_import"],"bank":"UNAVAILABLE" if not bank_latest else "FRESH","bank_observed_at":bank_latest},"reconciliations":self._reconciliation_counts(),"warnings":["Pilotage estimatif, pas une comptabilité certifiée"]}
  if self.sumup_transactions:
   payments=[r for r in self.sumup_transactions.rows() if start<=_iso(r['timestamp'])<end];payouts=self.sumup_settlements.rows() if self.sumup_settlements else []
   gross=sum((_d(r['amount']) for r in payments if str(r['status']).upper() not in {'FAILED','CANCELLED'}),Decimal());fees=sum((_d(r['fee']) for r in payments),Decimal())
   # Refund/chargeback totals are event-derived columns on the original payment;
   # do not infer them from transaction status and accidentally count gross twice.
   refunds=sum((_d(r.get('refunded_amount')) for r in payments),Decimal());chargebacks=sum((_d(r.get('chargeback_amount')) for r in payments),Decimal())
   paid=sum((_d(r['amount']) for r in payouts if str(r['status']).upper() in {'PAID','SUCCESSFUL','COMPLETED'}),Decimal());pending=sum((_d(r['amount']) for r in payouts if str(r['status']).upper() not in {'PAID','SUCCESSFUL','COMPLETED','FAILED'}),Decimal())
   result['payments']={'gross':_money(gross,source='SumUp Transaction Ledger'),'fees':_money(fees,source='SumUp Transaction Ledger'),'refunds':_money(refunds,source='SumUp Transaction Ledger'),'chargebacks':_money(chargebacks,source='SumUp Transaction Ledger'),'net':_money(gross-fees-refunds-chargebacks,source='SumUp Transaction Ledger'),'payouts_pending':_money(pending,source='Payment Settlement Ledger'),'payouts_paid':_money(paid,source='Payment Settlement Ledger'),'revenue_included':False}
  # Compatibility for the original cockpit/dashboard consumers.
  result.update(inflows_30d=str(credits),outflows_30d=str(debits),net_cashflow_30d=str(credits-debits),sales_revenue_30d=str(ttc) if ttc is not None else None,vat_collected=str(collected) if tax_available else "unavailable",vat_deductible="unavailable",unknown_flows=sum(t["category"]=="UNKNOWN" for t in tx),bank_fees=str(-_d(by_category["BANK_FEE"])),refunds=str(refunds),current_balance=result["current_balance"]["value"])
  return result

 def _has_table(self,name):
  return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
 def _latest(self,table,column):
  if not self._has_table(table): return None
  row=self.db.execute(f"SELECT max({column}) FROM {table}").fetchone(); return row[0] if row else None
 def _fresh_state(self,stamp,now,stale_hours=48):
  if not stamp: return "UNAVAILABLE"
  try: return "FRESH" if now-_iso(stamp) <= timedelta(hours=stale_hours) else "STALE"
  except Exception: return "UNKNOWN"
 def _metric(self,value,source,period=None,coverage="UNKNOWN",freshness="UNKNOWN",currency="EUR",method="",excluded=None):
  state="UNKNOWN" if value is None else "ZERO_REEL" if Decimal(str(value))==0 else "FRESH"
  return {"value":str(value) if value is not None else None,"currency":currency,"state":state,"coverage":coverage,"freshness":freshness,"source":source,"period":period,"method":method,"excluded":excluded}
 def _events_between(self,start,end):
  if not self._has_table('sale_events'): return []
  return [dict(r) for r in self.db.execute("SELECT * FROM sale_events WHERE sold_at>=? AND sold_at<?",(start.isoformat().replace("+00:00","Z"),end.isoformat().replace("+00:00","Z")))]
 def _sale_amount(self,rows):
  if not rows: return Decimal('0')
  if any(r.get('line_total_ttc') is None for r in rows): return None
  effect=lambda r: Decimal(1) if r.get('event_kind') in {'SALE','ADJUSTMENT'} else Decimal(-1)
  return sum((_d(r.get('line_total_ttc'))*effect(r) for r in rows),Decimal())
 def _source_range(self,source=None):
  if not self._has_table('sale_events'): return {"start":None,"end":None,"availability":"UNAVAILABLE"}
  if source:
   row=self.db.execute("SELECT min(sold_at),max(sold_at),count(*) FROM sale_events WHERE source=?",(source,)).fetchone()
  else: row=self.db.execute("SELECT min(sold_at),max(sold_at),count(*) FROM sale_events").fetchone()
  return {"start":row[0],"end":row[1],"availability":"FRESH" if row[2] else "UNAVAILABLE"}
 def finance_cockpit(self,now=None):
  """Build an independently recoverable read model for every finance block.

  This deliberately does not call :meth:`summary`: a missing Qonto schema (or
  any other optional ledger) must not prevent sales and SumUp from rendering.
  """
  now=now or datetime.now(timezone.utc); today=now.replace(hour=0,minute=0,second=0,microsecond=0); yesterday=today-timedelta(days=1); week=today-timedelta(days=today.weekday()); month=today.replace(day=1)
  warnings=[]
  def section(name, build):
   try:
    value=build(); value.setdefault('status','UNKNOWN'); value.setdefault('freshness',value['status']); value.setdefault('coverage','UNKNOWN'); value.setdefault('warning',None); value.setdefault('error_code',None); return value
   except Exception as exc:
    # Do not log SQL, row contents, credentials, or exception text: all can
    # contain production identifiers.  The exception class is enough to act.
    code=f"{name.upper()}_{type(exc).__name__.upper()}"
    log.exception("finance cockpit section failed section=%s error_code=%s",name,code)
    warnings.append(f"{name}: données temporairement indisponibles ({code})")
    return {'status':'ERROR','value':None,'data':None,'freshness':'UNKNOWN','coverage':'UNKNOWN','warning':'Calcul de section impossible','error_code':code}
  def status_for(present, freshness='UNKNOWN', partial=False):
   if not present:return 'UNAVAILABLE'
   if partial:return 'PARTIAL'
   return freshness if freshness in {'FRESH','STALE'} else 'UNKNOWN'
  try:
   sales_exist=self._has_table('sale_events') and bool(self.db.execute("SELECT 1 FROM sale_events LIMIT 1").fetchone());latest_sales=self._latest('sale_events','imported_at')
  except Exception:
   sales_exist=False;latest_sales=None;warnings.append('sales: métadonnées indisponibles (SALES_SCHEMA_ERROR)')
  sales_fresh=self._fresh_state(latest_sales,now,getattr(self.sales,'stale_after_hours',48))
  periods={"today":(today,now),"yesterday":(yesterday,today),"week_to_date":(week,now),"month_to_date":(month,now),"previous_week":(week-timedelta(days=7),week),"previous_month":((month-timedelta(days=1)).replace(day=1),month)}
  def revenue_build():
   data={}
   for key,(start,end) in periods.items():
    rows=self._events_between(start,end); amount=self._sale_amount(rows) if sales_exist else None
    data[key]=self._metric(amount,'sale_events',{'start':start.isoformat(),'end':end.isoformat()},'FRESH' if rows else 'ZERO_REEL' if sales_exist else 'UNAVAILABLE',sales_fresh)
   def compare(a,b): return None if a is None or b is None or Decimal(str(b))==0 else str((Decimal(str(a))-Decimal(str(b)))/Decimal(str(b))*100)
   data['week_vs_previous_percent']=compare(data['week_to_date']['value'],data['previous_week']['value']);data['month_vs_previous_percent']=compare(data['month_to_date']['value'],data['previous_month']['value'])
   rows=self._events_between(month,now) if sales_exist else []; count=len({r['external_sale_id'] for r in rows}) if sales_exist else None; total=data['month_to_date']['value']; period=data['month_to_date']['period']
   data['sales_count']=self._metric(count,'sale_events',period,'FRESH' if sales_exist else 'UNAVAILABLE',sales_fresh,currency=None)
   data['average_basket']=self._metric(Decimal(str(total))/count if total is not None and count else None,'sale_events',period,'FRESH' if count else 'UNKNOWN',sales_fresh)
   return {**data,'status':status_for(sales_exist,sales_fresh),'freshness':sales_fresh,'coverage':'FRESH' if sales_exist else 'UNAVAILABLE','warning':None}
  revenue=section('revenue',revenue_build)
  def channels_build():
   rows=self._events_between(month,now) if sales_exist else []; data=[]
   for source,label in [('SHOPCAISSE','Magasin'),('PRESTASHOP','E-commerce')]:
    selected=[r for r in rows if r.get('source')==source]; known=bool(selected) or bool(self.db.execute('SELECT 1 FROM sale_events WHERE source=? LIMIT 1',(source,)).fetchone()) if sales_exist else False
    data.append({'channel':label,'source':source,'revenue':self._metric(self._sale_amount(selected) if known else None,'sale_events',revenue.get('month_to_date',{}).get('period'),'FRESH' if selected else 'ZERO_REEL' if known else 'UNAVAILABLE',sales_fresh),'sales_count':len({r['external_sale_id'] for r in selected}) if known else None})
   present=any(c['revenue']['value'] is not None for c in data); return {'status':status_for(present,sales_fresh,any(c['revenue']['value'] is None for c in data)),'data':data,'freshness':sales_fresh,'coverage':'PARTIAL' if any(c['revenue']['value'] is None for c in data) else 'FRESH','warning':None}
  channels=section('channels',channels_build)
  empty=lambda source:self._metric(None,source,coverage='UNAVAILABLE',freshness='UNAVAILABLE')
  def payments_build():
   result={'by_method':[],'sumup_collected':empty('sumup_transactions'),'sumup_payouts':empty('sumup_payouts'),'in_transit':empty('payment_settlements'),'shopcaisse_sumup_gap':empty('payment_settlements')}; present=[]
   if self.sumup_transactions and self._has_table('sumup_transactions'):
    rows=[r for r in self.sumup_transactions.rows() if r.get('timestamp') and month<=_iso(r['timestamp'])<now]; any_rows=bool(self.db.execute('SELECT 1 FROM sumup_transactions LIMIT 1').fetchone()); fresh=self._fresh_state(self._latest('sumup_transactions','imported_at'),now); amount=sum((_d(r['amount']) for r in rows if str(r.get('status')).upper() not in {'FAILED','CANCELLED','CANCELED'}),Decimal()) if any_rows else None; result['sumup_collected']=self._metric(amount,'sumup_transactions',coverage='FRESH' if any_rows else 'UNAVAILABLE',freshness=fresh);present.append(any_rows)
   if self.sumup_settlements and self._has_table('sumup_payouts'):
    rows=[r for r in self.sumup_settlements.rows() if r.get('payout_date') and month<=_iso(r['payout_date'])<now]; any_rows=bool(self.db.execute('SELECT 1 FROM sumup_payouts LIMIT 1').fetchone()); fresh=self._fresh_state(self._latest('sumup_payouts','imported_at'),now); paid=sum((_d(r['amount']) for r in rows if str(r.get('status')).upper() in {'PAID','SUCCESSFUL','COMPLETED'}),Decimal()) if any_rows else None; result['sumup_payouts']=self._metric(paid,'sumup_payouts',coverage='FRESH' if any_rows else 'UNAVAILABLE',freshness=fresh);present.append(any_rows)
   fresh='FRESH' if any(present) else 'UNAVAILABLE';return {**result,'status':status_for(any(present),fresh,not all(present)),'freshness':fresh,'coverage':'PARTIAL' if any(present) and not all(present) else fresh,'warning':None}
  payments=section('payments',payments_build)
  def margins_build():
   rows=[r for r in self._events_between(month,now) if r.get('event_kind')=='SALE' and r.get('line_total_ttc') is not None] if sales_exist else []; covered=[r for r in rows if r.get('cost_basis') is not None]; rev=sum((_d(r['line_total_ttc']) for r in covered),Decimal()); total=sum((_d(r['line_total_ttc']) for r in rows),Decimal()); costs=sum((_d(r['cost_basis'])*_d(r['quantity']) for r in covered),Decimal()); gross=rev-costs if covered else None; partial=bool(covered) and len(covered)<len(rows); cov=str(rev/total*100) if total else None
   state=status_for(bool(covered),sales_fresh,partial); coverage='PARTIAL' if partial else 'FRESH' if covered else 'UNAVAILABLE'
   return {'status':state,'freshness':sales_fresh,'coverage':coverage,'warning':'Marge limitée aux ventes avec cost_basis.' if partial else None,'gross_margin':self._metric(gross,'sale_events.cost_basis',coverage=coverage,freshness=sales_fresh),'cost_of_goods_sold':self._metric(costs if covered else None,'sale_events.cost_basis',coverage=coverage,freshness=sales_fresh),'margin_rate_percent':self._metric(gross/rev*100 if gross is not None and rev else None,'sale_events.cost_basis',coverage=coverage,freshness=sales_fresh),'sales_without_known_cost':self._metric(total-rev if rows else None,'sale_events',coverage='FRESH' if rows else 'UNAVAILABLE',freshness=sales_fresh),'cost_basis_coverage_percent':cov,'excluded_sales_count':len(rows)-len(covered),'_rows':rows}
  margins=section('margins',margins_build)
  def products_build():
   grouped={}
   for row in margins.get('_rows') or []:
    key=row.get('product_key') or 'UNKNOWN'; item=grouped.setdefault(key,{'revenue':Decimal(),'cost':Decimal(),'covered':True});item['revenue']+=_d(row['line_total_ttc']);item['covered'] &= row.get('cost_basis') is not None;item['cost']+=_d(row.get('cost_basis'))*_d(row.get('quantity'))
   data=[{'product_key':k,'revenue':str(v['revenue']),'gross_margin':str(v['revenue']-v['cost']) if v['covered'] else None,'coverage':'FRESH' if v['covered'] else 'UNKNOWN'} for k,v in grouped.items()];return {'status':status_for(bool(data),sales_fresh),'freshness':sales_fresh,'coverage':'FRESH' if data else 'UNAVAILABLE','warning':None,'top_by_revenue':sorted(data,key=lambda x:Decimal(x['revenue']),reverse=True)[:10],'top_by_margin':sorted([x for x in data if x['gross_margin'] is not None],key=lambda x:Decimal(x['gross_margin']),reverse=True)[:10]}
  products=section('products',products_build)
  def settlements_build():
   tx=payments.get('sumup_collected',empty('sumup_transactions')); payouts=payments.get('sumup_payouts',empty('sumup_payouts')); present=tx.get('value') is not None or payouts.get('value') is not None
   transit=empty('payment_settlements'); matched=None
   if self._has_table('sumup_transactions') and self._has_table('payment_settlements'):
    row=self.db.execute("SELECT coalesce(sum(CAST(t.amount AS NUMERIC)),0),count(*) FROM sumup_transactions t LEFT JOIN payment_settlements p ON p.sumup_transaction_id=t.sumup_transaction_id WHERE p.settlement_id IS NULL AND upper(coalesce(t.status,'')) NOT IN ('FAILED','CANCELLED','CANCELED')").fetchone();transit=self._metric(Decimal(str(row[0])) if row[1] else None,'sumup_transactions/payment_settlements',coverage='PARTIAL',freshness=payments.get('freshness'));matched=self.db.execute('SELECT count(*) FROM payment_settlements').fetchone()[0]
   return {'status':status_for(present,payments.get('freshness','UNKNOWN'),True),'freshness':payments.get('freshness','UNKNOWN'),'coverage':'PARTIAL' if present else 'UNAVAILABLE','warning':'Rapprochement bancaire incomplet sans Qonto.' if present else None,'sales_expected':revenue.get('month_to_date'),'transactions_collected':tx,'payouts_received':payouts,'in_transit':transit,'unreconciled_gaps':None,'open_anomalies':None,'reconciliation_rate':matched}
  settlements=section('settlements',settlements_build)
  def bank_build():
   configured=bool(getattr(self,'qonto_configured',False))
   if not configured:return {'status':'NOT_CONFIGURED','freshness':'UNKNOWN','coverage':'UNAVAILABLE','warning':'Credential Qonto absent ou invalide.','provider':'Qonto','treasury':'NON CONFIGURÉ','balance':None,'error_code':'QONTO_NOT_CONFIGURED'}
   balances=self.bank.balances(); value=sum((_d(x['current_balance']) for x in balances),Decimal()) if balances else None;fresh=self._fresh_state(max((x['imported_at'] for x in balances),default=None),now)
   return {'status':status_for(bool(balances),fresh),'freshness':fresh,'coverage':'FRESH' if balances else 'UNAVAILABLE','warning':None,'provider':'Qonto','treasury':'CONFIGURED','balance':self._metric(value,'bank_accounts',coverage='FRESH',freshness=fresh) if value is not None else None}
  bank=section('bank',bank_build);margins.pop('_rows',None)
  blocks=[revenue,channels,payments,margins,settlements,products,bank];usable=any(x.get('status') in {'FRESH','PARTIAL','STALE'} for x in blocks);all_fresh=all(x.get('status')=='FRESH' for x in blocks)
  warnings[:0]=['Aucun faux zéro: les inconnues restent NULL/UNKNOWN/INDISPONIBLE.']+[x['warning'] for x in blocks if x.get('warning')]
  def safe_meta(call, fallback):
   try:return call()
   except Exception:return fallback
  coverage={'shopcaisse_sales':safe_meta(lambda:self._source_range('SHOPCAISSE'),{'start':None,'end':None,'availability':'UNKNOWN'}),'prestashop_sales':safe_meta(lambda:self._source_range('PRESTASHOP'),{'start':None,'end':None,'availability':'UNKNOWN'}),'sumup_transactions':{'end':safe_meta(lambda:self._latest('sumup_transactions','timestamp'),None),'availability':payments.get('coverage')},'sumup_payouts':{'end':safe_meta(lambda:self._latest('sumup_payouts','payout_date'),None),'availability':payments.get('coverage')},'qonto':{'availability':bank['status']},'cost_basis':{'availability':margins['coverage']}}
  return {'status':'FRESH' if all_fresh else 'PARTIAL' if usable else 'UNAVAILABLE','checked_at':now.isoformat(),'coverage':coverage,'freshness':{'sales':sales_fresh,'sumup_transactions':payments.get('freshness'),'sumup_payouts':payments.get('freshness'),'bank':bank['freshness']},'revenue':revenue,'channels':channels,'payments':payments,'margins':margins,'settlements':settlements,'products':products,'bank':bank,'warnings':list(dict.fromkeys(warnings))}

 def snapshot(self,now=None): return self.summary(30,now)
 def cashflow(self,days=30,now=None):
  summary=self.summary(days,now);return {"period":summary["period"],"cashflow":summary["cashflow"],"balances":summary["balances"],"freshness":summary["freshness"]}
 def tax(self,days=30,now=None):
  s=self.summary(days,now);return {"period":s["period"],"tax":s["tax"],"freshness":s["freshness"]}
 def profitability(self,days=30,now=None):
  s=self.summary(days,now); evidence=self.purchase_costs.profitability(s["period"]["start"],s["period"]["end"]) if self.purchase_costs else None
  return {"period":s["period"],"profitability":s["profitability"],"purchase_cost_evidence":evidence,"stock_value":self.purchase_costs.stock_value() if self.purchase_costs else None,"freshness":s["freshness"]}
 def _reconciliation_counts(self):
  rows=self.db.execute("SELECT status,count(*) n FROM reconciliation_matches GROUP BY status").fetchall() if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='reconciliation_matches'").fetchone() else []
  values={x:0 for x in ("MATCHED","POSSIBLE","UNMATCHED","CONFLICT")};values.update({r["status"]:r["n"] for r in rows});return values
