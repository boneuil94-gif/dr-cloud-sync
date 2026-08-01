from datetime import datetime,timezone
from decimal import Decimal
from dr_cloud_sync.bank import BankLedger,BankTransaction,TransactionPage
from dr_cloud_sync.finance import FinanceProjection
from dr_cloud_sync.sales import SalesLedger,SaleEvent

class Catalogue:
 def all(self): return []

def event(kind,amount,ht=None,cost=None):
 return SaleEvent('SHOPCAISSE','order','line-'+kind,datetime.now(timezone.utc).isoformat(),'UTC',kind,None,Decimal('1'),SalesLedger.key('SHOPCAISSE','order','line-'+kind,kind),line_total_ttc=Decimal(amount),line_total_ht=Decimal(ht) if ht else None,cost_basis=Decimal(cost) if cost else None,currency='EUR')

def test_finance_never_double_counts_sales_and_bank(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p);sales.append(event('SALE','100','83.33','50'));sales.db.commit()
 bank.import_page('Qonto',TransactionPage([BankTransaction('a',datetime.now(timezone.utc).isoformat(),Decimal('100'),'EUR','CB',external_transaction_id='b',category='SALES_SETTLEMENT')],None))
 s=FinanceProjection(bank,sales).summary()
 assert s['revenue']['ttc']['value']=='100' and s['cashflow']['inflows']['value']=='100'
 assert s['sales_revenue_30d']=='100' and s['profitability']['gross_margin']['value']=='50'
 assert s['tax']['collected']['available'] and s['tax']['collected']['value']=='16.67'

def test_unknown_tax_and_transfer_exclusion(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p);sales.append(event('SALE','100'));sales.db.commit()
 bank.import_page('Qonto',TransactionPage([BankTransaction('a',datetime.now(timezone.utc).isoformat(),Decimal('-25'),'EUR','Virement',external_transaction_id='t',category='TRANSFER')],None))
 s=FinanceProjection(bank,sales).summary()
 assert not s['tax']['collected']['available'];assert s['vat_collected']=='unavailable';assert s['cashflow']['outflows']['value']=='0'
 assert not s['profitability']['available'] and s['profitability']['gross_margin']['value'] is None

def test_bank_classification_is_explicit(tmp_path):
 bank=BankLedger(tmp_path/'db.sqlite');bank.import_page('Qonto',TransactionPage([BankTransaction('a',datetime.now(timezone.utc).isoformat(),Decimal('-5'),'EUR','fee',external_transaction_id='f')],None))
 row=bank.transactions()[0];updated=bank.classify(row['transaction_id'],'BANK_FEE',confirmed=True)
 assert updated['category']=='BANK_FEE' and updated['category_state']=='CONFIRMED'
