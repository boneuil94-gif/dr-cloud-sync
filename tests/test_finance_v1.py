from datetime import datetime,timezone
from decimal import Decimal
from dr_cloud_sync.bank import BankLedger,BankTransaction,TransactionPage
from dr_cloud_sync.finance import FinanceProjection
from dr_cloud_sync.sales import SalesLedger,SaleEvent
from dr_cloud_sync.sumup import PaymentSettlementLedger,SumUpPage,SumUpTransactionLedger
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
 assert "s.bank" in script and "m.value!==null" in script and "productCard.hidden=!revenue.length&&!margin.length" in script
 assert "s.payments.by_method" in script and "s.products.top_by_margin" in script

def test_finance_api_returns_http_200_for_partial_payload(configured):
 from test_os_production import login, request
 app,_=configured;_,cookie=login(app)
 app.finance.finance_cockpit=lambda:{'status':'PARTIAL','checked_at':'2026-08-08T00:00:00+00:00','revenue':{'status':'FRESH'},'bank':{'status':'NOT_CONFIGURED'}}
 status,_,body=request(app,'/api/finance/cockpit',cookie=cookie)
 assert status=='200 OK' and __import__('json').loads(body)['status']=='PARTIAL'

def test_finance_cockpit_production_like_partial_sources(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p)
 now=datetime.now(timezone.utc).replace(hour=12,minute=0,second=0,microsecond=0)
 def add(source,sale,line,amount,product,cost=None):
  sales.append(SaleEvent(source,sale,line,now.isoformat(),'UTC','SALE',product,Decimal('1'),SalesLedger.key(source,sale,line,'SALE'),line_total_ttc=Decimal(amount),cost_basis=Decimal(cost) if cost is not None else None,currency='EUR',channel='STORE' if source=='SHOPCAISSE' else 'ECOMMERCE'))
 add('SHOPCAISSE','shared','shop-1','100','shop-product','40')
 add('PRESTASHOP','shared','web-1','50','web-product')
 sales.db.executescript("CREATE TABLE sale_payments(payment_id TEXT PRIMARY KEY,sale_id TEXT,external_payment_id TEXT,payment_type TEXT,amount TEXT,name TEXT,description TEXT,canonical_payment_type TEXT,currency TEXT,occurred_at TEXT,status TEXT,source TEXT,store_id TEXT,imported_at TEXT,quality_status TEXT,quality_reason TEXT);")
 sales.db.execute("INSERT INTO sale_payments(payment_id,amount,canonical_payment_type,occurred_at,source,quality_status) VALUES('card','80','CARD',?,'SHOPCAISSE','VALID'),('cash','20','CASH',?,'SHOPCAISSE','VALID')",(now.isoformat(),now.isoformat()));sales.db.commit()
 transactions=SumUpTransactionLedger(bank.db);payouts=PaymentSettlementLedger(bank.db)
 transactions.import_page(SumUpPage(({'id':'tx-settled','transaction_code':'A','timestamp':now.isoformat(),'amount':'60','currency':'EUR','status':'SUCCESSFUL'},{'id':'tx-transit','transaction_code':'B','timestamp':now.isoformat(),'amount':'20','currency':'EUR','status':'SUCCESSFUL'}),None))
 payouts.import_page(SumUpPage(({'id':'payout-1','payout_date':now.isoformat(),'amount':'60','currency':'EUR','fee':'0','status':'PAID','items':[{'transaction_code':'A','amount':'60','currency':'EUR'}]},),None))
 cockpit=FinanceProjection(bank,sales,sumup_transactions=transactions,sumup_settlements=payouts).finance_cockpit(now=now+__import__('datetime').timedelta(hours=1))
 assert cockpit['status']=='PARTIAL' and cockpit['bank']['status']=='NOT_CONFIGURED'
 assert cockpit['revenue']['month_to_date']['value']=='150' and cockpit['revenue']['sales_count']['value']=='2'
 assert cockpit['revenue']['average_basket']['value']=='75'
 assert [x['revenue']['value'] for x in cockpit['channels']['data']]==['100','50']
 assert {x['method']:x['amount']['value'] for x in cockpit['payments']['by_method']}=={'CARD':'80','CASH':'20'}
 assert cockpit['payments']['sumup_collected']['value']=='80' and cockpit['payments']['sumup_payouts']['value']=='60'
 assert cockpit['payments']['in_transit']['value']=='20'
 assert cockpit['margins']['gross_margin']['value']=='60' and cockpit['margins']['sales_without_known_cost']['value']=='50'
 assert cockpit['products']['status']=='PARTIAL' and cockpit['products']['top_by_margin'][0]['product_key']=='shop-product'
 assert cockpit['settlements']['sales_expected']['value']=='150'  # SumUp is evidence, never revenue.
 for section in ('revenue','channels','payments','margins','settlements','products','bank'):
  assert cockpit[section]['status']
 for metric in (cockpit['revenue']['today'],cockpit['payments']['sumup_collected'],cockpit['payments']['sumup_payouts']):
  assert metric['source'] and metric['freshness'] and metric['period']

def test_finance_sales_count_ignores_refund_events(tmp_path):
 p=tmp_path/'db.sqlite';sales=SalesLedger(p,Catalogue());bank=BankLedger(p);now=datetime.now(timezone.utc)
 for kind,amount,line in (('SALE','100','sale'),('REFUND','20','refund')):
  sales.append(SaleEvent('SHOPCAISSE','ticket',line,now.isoformat(),'UTC',kind,None,Decimal('1'),SalesLedger.key('SHOPCAISSE','ticket',line,kind),line_total_ttc=Decimal(amount),currency='EUR'))
 sales.db.commit();cockpit=FinanceProjection(bank,sales).finance_cockpit(now=now+__import__('datetime').timedelta(seconds=1))
 assert cockpit['revenue']['month_to_date']['value']=='80'
 assert cockpit['revenue']['sales_count']['value']=='1' and cockpit['revenue']['average_basket']['value']=='80'
