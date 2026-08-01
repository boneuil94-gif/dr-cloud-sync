"""Explainable, read-only projections over the existing business ledgers.

The projection owns no accounting ledger: sales, bank, purchasing and stock
remain authoritative.  Missing evidence is represented as unavailable, never 0.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib, json, sqlite3

CATEGORIES=("SALES_SETTLEMENT","SUPPLIER_PAYMENT","BANK_FEE","RENT","TAX","PAYROLL","FINANCING","TRANSFER","REFUND","OTHER","UNKNOWN")

def _d(value): return Decimal(str(value or 0))
def _iso(value): return datetime.fromisoformat(value.replace("Z","+00:00"))
def _money(value,available=True,currency="EUR",source="",method=""):
    return {"value":str(value) if available else None,"currency":currency,"available":available,"source":source,"method":method}

class FinanceProjection:
 def __init__(self,bank,sales,purchases=None):
  self.bank,self.sales,self.purchases=bank,sales,purchases; self.db=bank.db
  self.db.executescript("""
  CREATE TABLE IF NOT EXISTS finance_recurring_charges(charge_id TEXT PRIMARY KEY,label TEXT NOT NULL,category TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,frequency TEXT NOT NULL,next_due_at TEXT,status TEXT NOT NULL,vat_amount TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS finance_snapshots(fingerprint TEXT PRIMARY KEY,period_start TEXT NOT NULL,period_end TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
  """);self.db.commit()
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
  # Compatibility for the original cockpit/dashboard consumers.
  result.update(inflows_30d=str(credits),outflows_30d=str(debits),net_cashflow_30d=str(credits-debits),sales_revenue_30d=str(ttc) if ttc is not None else None,vat_collected=str(collected) if tax_available else "unavailable",vat_deductible="unavailable",unknown_flows=sum(t["category"]=="UNKNOWN" for t in tx),bank_fees=str(-_d(by_category["BANK_FEE"])),refunds=str(refunds),current_balance=result["current_balance"]["value"])
  return result
 def snapshot(self,now=None): return self.summary(30,now)
 def cashflow(self,days=30,now=None):
  summary=self.summary(days,now);return {"period":summary["period"],"cashflow":summary["cashflow"],"balances":summary["balances"],"freshness":summary["freshness"]}
 def tax(self,days=30,now=None):
  s=self.summary(days,now);return {"period":s["period"],"tax":s["tax"],"freshness":s["freshness"]}
 def profitability(self,days=30,now=None):
  s=self.summary(days,now);return {"period":s["period"],"profitability":s["profitability"],"freshness":s["freshness"]}
 def _reconciliation_counts(self):
  rows=self.db.execute("SELECT status,count(*) n FROM reconciliation_matches GROUP BY status").fetchall() if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='reconciliation_matches'").fetchone() else []
  values={x:0 for x in ("MATCHED","POSSIBLE","UNMATCHED","CONFLICT")};values.update({r["status"]:r["n"] for r in rows});return values
