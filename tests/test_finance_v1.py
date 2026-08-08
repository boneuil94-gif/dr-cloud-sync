from datetime import datetime,timezone
from decimal import Decimal
from dr_cloud_sync.bank import BankLedger,BankTransaction,TransactionPage
from dr_cloud_sync.finance import FinanceProjection
from dr_cloud_sync.sales import SalesLedger,SaleEvent
from test_os_production import configured  # noqa: F401

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

def test_finance_cockpit_no_data_and_qonto_unavailable(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p)
 c=FinanceProjection(bank,sales).finance_cockpit(now=datetime(2026,8,5,12,tzinfo=timezone.utc))
 assert c['status']=='UNAVAILABLE' and c['revenue']['today']['value'] is None
 assert c['bank']['status']=='NOT_CONFIGURED' and c['bank']['treasury']=='NON CONFIGURÉ' and c['bank']['balance'] is None
 assert c['margins']['gross_margin']['value'] is None

def test_finance_cockpit_partial_cost_basis_and_channels(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p)
 now=datetime(2026,8,5,12,tzinfo=timezone.utc)
 base=event('SALE','100','83.33','50')
 sales.append(SaleEvent(base.source,base.external_sale_id,base.external_line_id,now.isoformat(),base.timezone,base.kind,base.product_key,base.quantity,base.idempotency_key,line_total_ttc=base.line_total_ttc,line_total_ht=base.line_total_ht,cost_basis=base.cost_basis,currency='EUR'))
 sales.append(SaleEvent('PRESTASHOP','web','l1',now.isoformat(),'UTC','SALE',None,Decimal('1'),SalesLedger.key('PRESTASHOP','web','l1','SALE'),line_total_ttc=Decimal('50'),line_total_ht=Decimal('41.67'),currency='EUR',channel='ECOMMERCE'))
 sales.db.commit()
 c=FinanceProjection(bank,sales).finance_cockpit(now=now)
 assert c['channels']['data'][0]['revenue']['value']=='100'
 assert c['channels']['data'][1]['revenue']['value']=='50'
 assert c['margins']['gross_margin']['value']=='50'
 assert c['margins']['excluded_sales_count']==1
 assert c['margins']['sales_without_known_cost']['value']=='50'
 assert c['revenue']['average_basket']['value']=='75'

def test_finance_cockpit_unknown_amount_stays_unknown(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p)
 e=event('SALE','100','83.33','50')
 sales.append(SaleEvent(e.source,e.external_sale_id,e.external_line_id,e.sold_at,e.timezone,e.kind,e.product_key,e.quantity,e.idempotency_key,line_total_ttc=None,line_total_ht=e.line_total_ht,cost_basis=e.cost_basis,currency='EUR'))
 c=FinanceProjection(bank,sales).finance_cockpit()
 assert c['revenue']['today']['value'] is None
 assert c['revenue']['today']['state']=='UNKNOWN'
 assert c['warnings'][0].startswith('Aucun faux zéro')

def test_finance_true_zero_is_distinct_from_no_source(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p)
 old=event('SALE','10');sales.append(SaleEvent(**{**old.__dict__,'sold_at':'2025-01-01T00:00:00+00:00'}));sales.db.commit()
 c=FinanceProjection(bank,sales).finance_cockpit(now=datetime(2026,8,5,12,tzinfo=timezone.utc))
 assert c['revenue']['today']['value']=='0' and c['revenue']['today']['state']=='ZERO_REEL'

def test_finance_section_exception_does_not_hide_sales(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p);sales.append(event('SALE','100'));sales.db.commit()
 from dr_cloud_sync.sumup import SumUpTransactionLedger
 broken=SumUpTransactionLedger(bank.db)
 bank.db.execute("INSERT INTO sumup_transactions(sumup_transaction_id,transaction_code,timestamp,amount,currency,status,fee,events_json,imported_at,raw_json) VALUES('x','x',?,'10','EUR','SUCCESSFUL','0','[]',?,'{}')",(datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat()))
 broken.rows=lambda: (_ for _ in ()).throw(RuntimeError('secret production detail'))
 cockpit=FinanceProjection(bank,sales,sumup_transactions=broken).finance_cockpit()
 assert cockpit['status']=='PARTIAL' and cockpit['revenue']['today']['value']=='100'
 assert cockpit['payments']['status']=='ERROR' and cockpit['payments']['error_code']=='PAYMENTS_RUNTIMEERROR'
 assert 'secret production detail' not in str(cockpit)

def test_finance_contract_has_status_on_every_section(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p);sales.append(event('SALE','0','0','0'));sales.db.commit()
 cockpit=FinanceProjection(bank,sales).finance_cockpit()
 for name in ('revenue','channels','payments','margins','settlements','products','bank'):
  assert {'status','freshness','coverage','warning','error_code'} <= cockpit[name].keys()
 assert cockpit['bank']['status']=='NOT_CONFIGURED'

def test_finance_cockpit_api_and_administration_finance_render(configured):
 from test_os_production import login, request
 app,_=configured; _,cookie=login(app)
 status,_,body=request(app,'/api/finance/cockpit',cookie=cookie)
 assert status=='200 OK'
 payload=__import__('json').loads(body)
 assert {'status','checked_at','coverage','freshness','revenue','channels','payments','margins','settlements','products','bank','warnings'} <= set(payload)
 status,_,body=request(app,'/finance',cookie=cookie)
 assert status=='200 OK'
 html=body.decode()
 assert 'Finance Cockpit' in html and 'Data Hub' in html and 'Aucun faux zéro' in html

def test_finance_frontend_renders_known_cards_without_fake_zero():
 from pathlib import Path
 script=(Path(__file__).parents[1]/'src/dr_cloud_sync/static/finance.js').read_text()
 assert "card('CA & activité',s.revenue" in script and "card('Paiements & SumUp',s.payments" in script
 assert "s.bank" in script and "m.value!==null" in script and "productCard.hidden=!known.length" in script

def test_finance_api_returns_http_200_for_partial_payload(configured):
 from test_os_production import login, request
 app,_=configured;_,cookie=login(app)
 app.finance.finance_cockpit=lambda:{'status':'PARTIAL','checked_at':'2026-08-08T00:00:00+00:00','revenue':{'status':'FRESH'},'bank':{'status':'NOT_CONFIGURED'}}
 status,_,body=request(app,'/api/finance/cockpit',cookie=cookie)
 assert status=='200 OK' and __import__('json').loads(body)['status']=='PARTIAL'
