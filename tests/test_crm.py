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
    assert crm.db.execute("select state from crm_identities where customer_id=?",(c['customer_id'],)).fetchone()[0]=='PROBABLE'

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


def test_customer_360_rfm_repurchase_coverage_and_consent(tmp_path: Path):
    db=tmp_path/'db.sqlite'; ledger=SalesLedger(db,Catalogue()); crm=CRMService(db)
    customer=crm.ingest_customer('SHOPCAISSE','customer-7',{'first_name':'Lina','email':'lina@example.test'})
    crm.record_consent(customer['customer_id'],'EMAIL','GRANTED','SHOPCAISSE',evidence='checkout checkbox')
    for index,(day,amount) in enumerate((('2026-01-01','100'),('2026-02-01','120'),('2026-03-04','140'))):
        event=SaleEvent('SHOPCAISSE',f'order-{index}','1',f'{day}T12:00:00+00:00','UTC','SALE','product:a',1,ledger.key('SHOPCAISSE',f'order-{index}','1','SALE'),line_total_ttc=amount,line_total_ht=amount,currency='EUR',channel='STORE')
        ledger.append(event)
        crm.link_sale(customer['customer_id'],ledger.db.execute('select sale_event_id from sale_events where idempotency_key=?',(event.idempotency_key,)).fetchone()[0],source='SHOPCAISSE')
    ledger.append(SaleEvent('PRESTASHOP','anonymous','1','2026-03-04T12:00:00+00:00','UTC','SALE',None,1,ledger.key('PRESTASHOP','anonymous','1','SALE'),line_total_ttc='40',currency='EUR'))
    crm.refresh_metrics(period_end='2026-03-20T12:00:00+00:00')
    view=crm.customer_360(customer['customer_id'],True)
    assert view['metrics']['orders_count']==3 and view['metrics']['historical_revenue_ttc']=='360'
    assert view['metrics']['segment']=='FIDÈLE'
    assert view['products'][0]['prediction_status']=='PREDICTED'
    assert view['favorite_categories'] is None and view['metrics']['historical_margin'] is None
    assert view['marketing']['marketing_consent']=='OPT_IN'
    evidence=crm.cockpit()
    assert evidence['coverage']['sales_customer_link_coverage']==.75
    assert evidence['coverage']['revenue_customer_link_coverage']==.9


def test_revoked_and_unknown_consent_never_opt_in(tmp_path: Path):
    crm=CRMService(tmp_path/'db.sqlite')
    unknown=crm.ingest_customer('PRESTASHOP','1',{'email':'unknown@example.test'})
    opted=crm.ingest_customer('PRESTASHOP','2',{'email':'opted@example.test','newsletter':True})
    crm.record_consent(opted['customer_id'],'EMAIL','WITHDRAWN','customer_request',evidence='request')
    assert crm.marketing_consent(unknown['customer_id'])['marketing_consent']=='UNKNOWN'
    revoked=crm.marketing_consent(opted['customer_id'])
    assert revoked['marketing_consent']=='REVOKED' and revoked['revoked_at'] is not None


def test_same_name_different_people_and_old_database_migration(tmp_path: Path):
    db=tmp_path/'old.sqlite'; sqlite3.connect(db).execute('create table legacy(value text)').connection.close()
    crm=CRMService(db)
    a=crm.ingest_customer('PRESTASHOP','a',{'first_name':'Camille','email':'one@example.test'})
    b=crm.ingest_customer('SHOPCAISSE','b',{'first_name':'Camille','email':'two@example.test'})
    assert a['customer_id']!=b['customer_id'] and crm.duplicate_candidates()==[]
    assert crm.rfm_config()['version']==1
