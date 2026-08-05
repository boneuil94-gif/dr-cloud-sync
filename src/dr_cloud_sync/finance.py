"""Explainable, read-only projections over the existing business ledgers.

The projection owns no accounting ledger: sales, bank, purchasing and stock
remain authoritative.  Missing evidence is represented as unavailable, never 0.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib, json, sqlite3
from .schema import ensure_schema

SCHEMA="""
CREATE TABLE IF NOT EXISTS finance_recurring_charges(charge_id TEXT PRIMARY KEY,label TEXT NOT NULL,category TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,frequency TEXT NOT NULL,next_due_at TEXT,status TEXT NOT NULL,vat_amount TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_snapshots(fingerprint TEXT PRIMARY KEY,period_start TEXT NOT NULL,period_end TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
"""

CATEGORIES=("SALES_SETTLEMENT","SUPPLIER_PAYMENT","BANK_FEE","RENT","TAX","PAYROLL","FINANCING","TRANSFER","REFUND","OTHER","UNKNOWN")

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
  now=now or datetime.now(timezone.utc); today=now.replace(hour=0,minute=0,second=0,microsecond=0); yesterday=today-timedelta(days=1); week=today-timedelta(days=today.weekday()); month=today.replace(day=1)
  latest_sales=self._latest('sale_events','imported_at'); sales_fresh=self._fresh_state(latest_sales,now,getattr(self.sales,'stale_after_hours',48))
  periods={"today":(today,now),"yesterday":(yesterday,today),"week_to_date":(week,now),"month_to_date":(month,now),"previous_week":(week-timedelta(days=7),week),"previous_month":((month-timedelta(days=1)).replace(day=1),month)}
  revenue={}
  for k,(a,b) in periods.items():
   rows=self._events_between(a,b); value=self._sale_amount(rows) if self._has_table('sale_events') else None; avail="FRESH" if rows else "ZERO_REEL" if self._has_table('sale_events') else "UNAVAILABLE"
   revenue[k]=self._metric(value,'sale_events',{"start":a.isoformat(),"end":b.isoformat()},avail,sales_fresh,method='Somme TTC des SALE/ADJUSTMENT moins REFUND/RETURN/CANCELLATION; NULL si une ligne fiable manque')
  def cmp(cur,prev):
   if cur is None or prev is None or Decimal(str(prev))==0: return None
   return str((Decimal(str(cur))-Decimal(str(prev)))/Decimal(str(prev))*100)
  revenue['week_vs_previous_percent']=cmp(revenue['week_to_date']['value'],revenue['previous_week']['value']); revenue['month_vs_previous_percent']=cmp(revenue['month_to_date']['value'],revenue['previous_month']['value'])
  all_month=self._events_between(month,now); sales_count=len({r['external_sale_id'] for r in all_month}) if self._has_table('sale_events') else None; total=revenue['month_to_date']['value']
  revenue['sales_count']=self._metric(sales_count,'sale_events',period=revenue['month_to_date']['period'],coverage='FRESH' if sales_count is not None else 'UNAVAILABLE',freshness=sales_fresh,currency=None)
  revenue['average_basket']=self._metric((Decimal(str(total))/Decimal(sales_count) if total is not None and sales_count else None),'sale_events',period=revenue['month_to_date']['period'],coverage='PARTIAL' if not sales_count else 'FRESH',freshness=sales_fresh,method='CA mois / ventes distinctes; NULL si division impossible')
  channels=[]
  for src,label in [('SHOPCAISSE','Magasin'),('PRESTASHOP','E-commerce')]:
   rows=[r for r in all_month if r.get('source')==src]; val=self._sale_amount(rows); channels.append({"channel":label,"source":src,"revenue":self._metric(val,'sale_events',revenue['month_to_date']['period'],'FRESH' if rows else 'ZERO_REEL',sales_fresh),"sales_count":len({r['external_sale_id'] for r in rows})})
  payments={"by_method":[],"sumup_collected":self._metric(None,'sumup_transactions',coverage='UNAVAILABLE',freshness='UNAVAILABLE'),"sumup_payouts":self._metric(None,'sumup_payouts',coverage='UNAVAILABLE',freshness='UNAVAILABLE'),"in_transit":self._metric(None,'payment_settlement_links',coverage='UNAVAILABLE',freshness='UNAVAILABLE'),"shopcaisse_sumup_gap":self._metric(None,'payment_settlement_links',coverage='UNAVAILABLE',freshness='UNAVAILABLE')}
  if self._has_table('sale_payments') and self._has_table('sales'):
   for r in self.db.execute("""SELECT p.canonical_payment_type,count(*) n,sum(CAST(p.amount AS NUMERIC)) amount FROM sale_payments p JOIN sales s USING(sale_id) WHERE p.quality_status='VALID' AND coalesce(p.occurred_at,s.sold_at)>=? AND coalesce(p.occurred_at,s.sold_at)<? GROUP BY p.canonical_payment_type ORDER BY amount DESC""",(month.isoformat().replace("+00:00","Z"),now.isoformat().replace("+00:00","Z"))):
    payments['by_method'].append({"method":r[0],"count":r[1],"amount":self._metric(Decimal(str(r[2] or 0)),'sale_payments',revenue['month_to_date']['period'],'FRESH',sales_fresh)})
  if self.sumup_transactions:
   tx=[r for r in self.sumup_transactions.rows() if month<=_iso(r['timestamp'])<now]; gross=sum((_d(r['amount']) for r in tx if str(r.get('status')).upper() not in {'FAILED','CANCELLED','CANCELED'}),Decimal()) if tx else Decimal('0')
   payments['sumup_collected']=self._metric(gross,'sumup_transactions',revenue['month_to_date']['period'],'FRESH' if tx else 'ZERO_REEL',self._fresh_state(self._latest('sumup_transactions','imported_at'),now))
  if self.sumup_settlements:
   pays=[r for r in self.sumup_settlements.rows() if r.get('payout_date') and month<=_iso(r['payout_date'])<now]; paid=sum((_d(r['amount']) for r in pays if str(r.get('status')).upper() in {'PAID','SUCCESSFUL','COMPLETED'}),Decimal()) if pays else Decimal('0'); payments['sumup_payouts']=self._metric(paid,'sumup_payouts',revenue['month_to_date']['period'],'FRESH' if pays else 'ZERO_REEL',self._fresh_state(self._latest('sumup_payouts','imported_at'),now))
  margin_rows=[r for r in all_month if r.get('event_kind')=='SALE' and r.get('line_total_ttc') is not None]; covered=[r for r in margin_rows if r.get('cost_basis') is not None]; covered_rev=sum((_d(r['line_total_ttc']) for r in covered),Decimal()); costs=sum((_d(r['cost_basis'])*_d(r['quantity']) for r in covered),Decimal()); gross=(covered_rev-costs) if covered else None; total_margin=sum((_d(r['line_total_ttc']) for r in margin_rows),Decimal()) if margin_rows else Decimal('0')
  margins={"gross_margin":self._metric(gross,'sale_events.cost_basis',revenue['month_to_date']['period'],'PARTIAL' if covered and len(covered)<len(margin_rows) else 'FRESH' if covered else 'UNKNOWN',sales_fresh,method='Calculé uniquement sur lignes avec cost_basis'),"cost_of_goods_sold":self._metric(costs if covered else None,'sale_events.cost_basis',revenue['month_to_date']['period'],'PARTIAL' if covered and len(covered)<len(margin_rows) else 'FRESH' if covered else 'UNKNOWN',sales_fresh),"margin_rate_percent":self._metric((gross/covered_rev*100 if gross is not None and covered_rev else None),'sale_events.cost_basis',revenue['month_to_date']['period'],'PARTIAL' if covered and len(covered)<len(margin_rows) else 'UNKNOWN',sales_fresh),"sales_without_known_cost":self._metric(total_margin-covered_rev,'sale_events',revenue['month_to_date']['period'],'FRESH' if margin_rows else 'ZERO_REEL',sales_fresh),"cost_basis_coverage_percent":str((covered_rev/total_margin*100) if total_margin else Decimal('0')),"excluded_sales_count":len(margin_rows)-len(covered)}
  products=[]
  by={}
  for r in margin_rows:
   key=r.get('product_key') or 'UNKNOWN'; by.setdefault(key,{"revenue":Decimal(),"cost":Decimal(),"covered":True}); by[key]['revenue']+=_d(r['line_total_ttc']); by[key]['covered'] &= r.get('cost_basis') is not None; by[key]['cost']+=_d(r.get('cost_basis'))*_d(r.get('quantity'))
  products=[{"product_key":k,"revenue":str(v['revenue']),"gross_margin":str(v['revenue']-v['cost']) if v['covered'] else None,"coverage":"FRESH" if v['covered'] else "UNKNOWN"} for k,v in by.items()]
  sett=self.sumup_settlements.cockpit() if self.sumup_settlements else None; bank_avail=bool(self._has_table('bank_accounts') and self.db.execute("SELECT 1 FROM bank_accounts WHERE provider='Qonto' LIMIT 1").fetchone())
  return {"status":"PARTIAL" if any(x in {'UNKNOWN','UNAVAILABLE','STALE'} for x in [sales_fresh]) else "FRESH","checked_at":now.isoformat(),"coverage":{"shopcaisse_sales":self._source_range('SHOPCAISSE'),"prestashop_sales":self._source_range('PRESTASHOP'),"sumup_transactions":{"end":self._latest('sumup_transactions','timestamp'),"availability":"FRESH" if self._has_table('sumup_transactions') else 'UNAVAILABLE'},"sumup_payouts":{"end":self._latest('sumup_payouts','payout_date'),"availability":"FRESH" if self._has_table('sumup_payouts') else 'UNAVAILABLE'},"qonto":{"availability":"FRESH" if bank_avail else "UNAVAILABLE","limitation":"Credential invalide/non configuré: aucune estimation de solde"},"cost_basis":{"availability":"PARTIAL" if margins['excluded_sales_count'] else "FRESH" if covered else "UNKNOWN"}},"freshness":{"sales":sales_fresh,"sumup_transactions":payments['sumup_collected']['freshness'],"sumup_payouts":payments['sumup_payouts']['freshness'],"bank":"FRESH" if bank_avail else "UNAVAILABLE"},"revenue":revenue,"channels":channels,"payments":payments,"margins":margins,"settlements":sett or {"status":"UNAVAILABLE","sales_expected":None,"transactions_collected":payments['sumup_collected'],"payouts_received":payments['sumup_payouts'],"in_transit":payments['in_transit'],"unreconciled_gaps":None,"open_anomalies":None,"reconciliation_rate":None},"products":{"top_by_revenue":sorted(products,key=lambda x:Decimal(x['revenue']),reverse=True)[:10],"top_by_margin":sorted([p for p in products if p['gross_margin'] is not None],key=lambda x:Decimal(x['gross_margin']),reverse=True)[:10]},"bank":{"provider":"Qonto","treasury":"INDISPONIBLE" if not bank_avail else "CONFIGURED","balance":None if not bank_avail else self.summary(now=now)['current_balance']},"warnings":["Aucun faux zéro: les inconnues restent NULL/UNKNOWN/INDISPONIBLE.","Marge calculée seulement sur cost_basis couvert."]}

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
