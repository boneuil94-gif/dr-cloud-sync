"""Read-only finance and cash-flow projections from authoritative ledgers."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
class FinanceProjection:
 def __init__(self,bank,sales,purchases=None): self.bank,self.sales,self.purchases=bank,sales,purchases
 def snapshot(self,now=None):
  now=now or datetime.now(timezone.utc);tx=self.bank.transactions();balances=self.bank.balances()
  def window(days,direction): return sum((Decimal(t['amount']) for t in tx if t['direction']==direction and datetime.fromisoformat(t['booked_at'])>=now-timedelta(days=days)),Decimal())
  revenue=Decimal(str(self.sales.metrics(None,30).get('revenue_ttc') or 0));in30=window(30,'CREDIT');out30=-window(30,'DEBIT')
  unknown=sum(t['category']=='UNKNOWN' for t in tx);matched=self.bank.db.execute("SELECT count(*) FROM reconciliation_matches WHERE status='MATCHED'").fetchone()[0] if self.bank.db.execute("SELECT 1 FROM sqlite_master WHERE name='reconciliation_matches'").fetchone() else 0
  return {'balances':balances,'current_balance':str(sum((Decimal(b['current_balance']) for b in balances),Decimal())) if balances else None,'inflows_7d':str(window(7,'CREDIT')),'outflows_7d':str(-window(7,'DEBIT')),'inflows_30d':str(in30),'outflows_30d':str(out30),'net_cashflow_30d':str(in30-out30),'sales_revenue_30d':str(revenue),'sales_cash_difference_30d':str(revenue-in30),'bank_fees':str(-sum((Decimal(t['amount']) for t in tx if t['category']=='BANK_FEE'),Decimal())),'refunds':str(sum((Decimal(t['amount']) for t in tx if t['category']=='REFUND'),Decimal())),'unknown_flows':unknown,'reconciled':matched,'vat_collected':'unavailable','vat_deductible':'unavailable'}
