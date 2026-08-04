from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from dr_cloud_sync.crm import CRMService, normalise_email, normalise_phone
from dr_cloud_sync.sales import SaleEvent, SalesLedger

class Catalogue:
    def all(self): return []

def test_prudent_identity_consent_and_idempotence(tmp_path: Path):
    crm=CRMService(tmp_path/'db.sqlite')
    a=crm.ingest_customer('PRESTASHOP','10',{'first_name':'Ada','email':' ADA@Example.COM ','phone':'06 12 34 56 78','newsletter':False})
    replay=crm.ingest_customer('PRESTASHOP','10',{'first_name':'Ada','email':'ada@example.com','phone':'0612345678'})
    assert replay['customer_id']==a['customer_id']
    assert a['consents']['EMAIL']=='DENIED'
    assert normalise_email('bad') is None and normalise_phone('12') is None
    # Name-only identity never merges.
    b=crm.ingest_customer('SHOPCAISSE','20',{'first_name':'Ada'})
    assert b['customer_id'] != a['customer_id']

def test_exact_email_and_phone_merge_but_single_field_is_candidate(tmp_path: Path):
    crm=CRMService(tmp_path/'db.sqlite')
    a=crm.ingest_customer('PRESTASHOP','1',{'email':'a@example.test','phone':'+33600000000'})
    b=crm.ingest_customer('SHOPCAISSE','2',{'email':'a@example.test','phone':'+33600000000'})
    assert a['customer_id']==b['customer_id']
    c=crm.ingest_customer('OTHER','3',{'email':'a@example.test'})
    assert c['customer_id']!=a['customer_id']
    assert crm.db.execute("select state from crm_identities where customer_id=?",(c['customer_id'],)).fetchone()[0]=='POSSIBLE'

def test_sales_ledger_is_only_revenue_authority_and_anonymous_stays_anonymous(tmp_path: Path):
    db=tmp_path/'db.sqlite'; ledger=SalesLedger(db,Catalogue()); crm=CRMService(db)
    stamp=datetime.now(timezone.utc).isoformat()
    for sale,amount in [('linked','100'),('anonymous','50')]:
        ledger.append(SaleEvent('PRESTASHOP',sale,'1',stamp,'UTC','SALE',None,1,ledger.key('PRESTASHOP',sale,'1','SALE'),line_total_ttc=amount,currency='EUR'))
    ledger.db.commit()
    customer=crm.ingest_customer('PRESTASHOP','1',{'email':'buyer@example.test','phone':'+33600000000'})
    event=ledger.db.execute("select sale_event_id from sale_events where external_sale_id='linked'").fetchone()[0]
    crm.link_sale(customer['customer_id'],event,source='PRESTASHOP')
    assert crm.refresh_metrics()['revenue_authority']=='SALES_LEDGER'
    cockpit=crm.cockpit(); assert cockpit['attributed_revenue_ttc']==100 and cockpit['anonymous_sales']==1
    assert crm.db.execute('select count(*) from crm_customers').fetchone()[0]==1

def test_loyalty_is_append_only_simulation_and_unknown_blocks_campaign(tmp_path: Path):
    crm=CRMService(tmp_path/'db.sqlite'); c=crm.ingest_customer('INTERNAL','1',{'display_name':'Sans contact'})
    assert crm.loyalty_simulate(c['customer_id'],'20.90',points_per_euro=2)['points']==41
    balance=crm.loyalty_transaction(c['customer_id'],'EARN',10,'sale:1','simulation')['balance']; assert balance==10
    campaign=crm.create_campaign('Test',None,'Review','No send','EMAIL')
    result=crm.review_campaign(campaign,[c['customer_id']])
    assert result['status']=='BLOCKED_CONSENT' and result['external_send'] is False
