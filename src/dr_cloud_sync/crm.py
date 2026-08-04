"""Privacy-first CRM and loyalty foundation built on the Sales Ledger.

Customer identity is deliberately separate from commercial events.  The module
only links a sale using an explicit source reference; anonymous ledger rows stay
anonymous and are still included in ledger revenue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

CONSENT_STATES={"GRANTED","DENIED","WITHDRAWN","UNKNOWN","NOT_APPLICABLE"}
CONSENT_CHANNELS={"EMAIL","SMS","PHONE","POSTAL","SOCIAL","PUSH"}
QUALITY_STATES={"COMPLETE","PARTIAL","LOW_QUALITY","CONFLICT","ANONYMOUS"}
IDENTITY_STATES={"MATCHED","POSSIBLE","CONFLICT","SEPARATE","ANONYMOUS"}
CAMPAIGN_STATES={"DRAFT","READY_FOR_REVIEW","APPROVED","BLOCKED_CONSENT","BLOCKED_PROVIDER","SCHEDULED_INTERNAL","MEASURED","CANCELLED"}

SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS crm_customers(
 customer_id TEXT PRIMARY KEY,display_name TEXT,first_name TEXT,last_name TEXT,
 email_normalized TEXT,phone_normalized TEXT,birth_date TEXT,language TEXT,country TEXT,
 city TEXT,postal_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 status TEXT NOT NULL,primary_source TEXT NOT NULL,data_quality TEXT NOT NULL,
 quality_details_json TEXT NOT NULL,anonymised_at TEXT,deleted_at TEXT,merged_into TEXT);
CREATE INDEX IF NOT EXISTS ix_crm_customer_email ON crm_customers(email_normalized) WHERE email_normalized IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_crm_customer_phone ON crm_customers(phone_normalized) WHERE phone_normalized IS NOT NULL;
CREATE TABLE IF NOT EXISTS crm_external_references(
 reference_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),
 provider TEXT NOT NULL,external_id TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
 raw_identity_fingerprint TEXT,confidence REAL NOT NULL,link_method TEXT NOT NULL,
 manually_verified INTEGER NOT NULL DEFAULT 0,UNIQUE(provider,external_id));
CREATE INDEX IF NOT EXISTS ix_crm_external_customer ON crm_external_references(customer_id);
CREATE TABLE IF NOT EXISTS crm_identities(
 identity_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),
 candidate_customer_id TEXT REFERENCES crm_customers(customer_id),state TEXT NOT NULL,
 evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,resolved_at TEXT);
CREATE TABLE IF NOT EXISTS crm_addresses(address_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),provider TEXT,external_id TEXT,address1 TEXT,address2 TEXT,city TEXT,postal_code TEXT,country TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(provider,external_id));
CREATE TABLE IF NOT EXISTS crm_consents(consent_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),channel TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,observed_at TEXT NOT NULL,evidence TEXT,purpose TEXT NOT NULL,policy_version TEXT,expires_at TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_crm_consent ON crm_consents(customer_id,channel,observed_at DESC);
CREATE TABLE IF NOT EXISTS crm_tags(tag_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),tag TEXT NOT NULL,is_system INTEGER NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,deleted_at TEXT,UNIQUE(customer_id,tag));
CREATE TABLE IF NOT EXISTS crm_segments(segment_id TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL,rules_json TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,UNIQUE(name,version));
CREATE TABLE IF NOT EXISTS crm_segment_memberships(membership_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),segment_id TEXT NOT NULL REFERENCES crm_segments(segment_id),explanation_json TEXT NOT NULL,calculated_at TEXT NOT NULL,valid_until TEXT,UNIQUE(customer_id,segment_id));
CREATE INDEX IF NOT EXISTS ix_crm_membership_customer ON crm_segment_memberships(customer_id);
CREATE TABLE IF NOT EXISTS crm_sale_links(link_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),sale_event_id TEXT NOT NULL UNIQUE,link_method TEXT NOT NULL,confidence REAL NOT NULL,source TEXT NOT NULL,linked_at TEXT NOT NULL,manually_verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS crm_metric_snapshots(snapshot_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),period_start TEXT,period_end TEXT NOT NULL,first_order_at TEXT,last_order_at TEXT,order_count INTEGER,ticket_count INTEGER,revenue_ttc TEXT,revenue_ht TEXT,average_basket TEXT,purchase_frequency REAL,average_interval_days REAL,units TEXT,refunds_ttc TEXT,cancellations INTEGER,main_channel TEXT,main_store TEXT,rfm_json TEXT NOT NULL,calculated_at TEXT NOT NULL,UNIQUE(customer_id,period_end));
CREATE INDEX IF NOT EXISTS ix_crm_metric_last_order ON crm_metric_snapshots(last_order_at);
CREATE TABLE IF NOT EXISTS crm_activities(activity_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),activity_type TEXT NOT NULL,occurred_at TEXT NOT NULL,source TEXT NOT NULL,actor TEXT,result TEXT,metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_interactions(interaction_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES crm_customers(customer_id),kind TEXT NOT NULL,content TEXT NOT NULL,author TEXT NOT NULL,visibility TEXT NOT NULL,status TEXT NOT NULL,due_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
CREATE TABLE IF NOT EXISTS loyalty_accounts(account_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL UNIQUE REFERENCES crm_customers(customer_id),tier TEXT NOT NULL,simulation INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS loyalty_transactions(transaction_id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES loyalty_accounts(account_id),kind TEXT NOT NULL,points INTEGER NOT NULL,source_reference TEXT,idempotency_key TEXT NOT NULL UNIQUE,occurred_at TEXT NOT NULL,expires_at TEXT,reverses_transaction_id TEXT REFERENCES loyalty_transactions(transaction_id),reason TEXT NOT NULL,actor TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_loyalty_account_date ON loyalty_transactions(account_id,occurred_at);
CREATE TRIGGER IF NOT EXISTS loyalty_transactions_no_update BEFORE UPDATE ON loyalty_transactions BEGIN SELECT RAISE(ABORT,'loyalty ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS loyalty_transactions_no_delete BEFORE DELETE ON loyalty_transactions BEGIN SELECT RAISE(ABORT,'loyalty ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS crm_recommendations(recommendation_id TEXT PRIMARY KEY,customer_id TEXT REFERENCES crm_customers(customer_id),reason TEXT NOT NULL,evidence_json TEXT NOT NULL,priority TEXT NOT NULL,allowed_channel TEXT,product_key TEXT,limitation TEXT,confidence REAL NOT NULL,next_action TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_campaigns(campaign_id TEXT PRIMARY KEY,name TEXT NOT NULL,segment_id TEXT,objective TEXT NOT NULL,content TEXT NOT NULL,planned_channel TEXT NOT NULL,consent_required INTEGER NOT NULL,status TEXT NOT NULL,scheduled_at TEXT,measurement_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_merge_history(merge_id TEXT PRIMARY KEY,primary_customer_id TEXT NOT NULL,secondary_customer_id TEXT NOT NULL,snapshot_json TEXT NOT NULL,merged_at TEXT NOT NULL,merged_by TEXT NOT NULL,reversed_at TEXT,reversed_by TEXT);
"""

def now() -> str: return datetime.now(timezone.utc).isoformat()
def normalise_email(value: str|None) -> str|None:
    value=(value or "").strip().casefold()
    return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",value) else None
def normalise_phone(value: str|None) -> str|None:
    digits=re.sub(r"\D","",value or "")
    if digits.startswith("00"): digits=digits[2:]
    if digits.startswith("0") and len(digits)==10: digits="33"+digits[1:]
    return "+"+digits if 8 <= len(digits) <= 15 else None
def mask_email(value: str|None) -> str|None:
    if not value:return None
    local,domain=value.split("@",1); return (local[:1]+"***@"+domain)
def mask_phone(value: str|None) -> str|None:
    return None if not value else "•"*max(0,len(value)-4)+value[-4:]

class CRMService:
    def __init__(self,path: Path|str|sqlite3.Connection):
        if isinstance(path,sqlite3.Connection):
            self.path=None; self.db=path
        else:
            self.path=Path(path); self.db=sqlite3.connect(self.path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        with self.db:self.db.executescript(SCHEMA)

    def ingest_customer(self,provider: str,external_id: str,record: Mapping[str,Any]) -> dict[str,Any]:
        """Idempotently ingest one explicit source identity; names never cause merging."""
        provider=provider.strip().upper(); external_id=str(external_id).strip()
        if not provider or not external_id: raise ValueError("provider and external_id are required")
        stamp=now(); email=normalise_email(record.get("email")); phone=normalise_phone(record.get("phone"))
        ref=self.db.execute("SELECT customer_id FROM crm_external_references WHERE provider=? AND external_id=?",(provider,external_id)).fetchone()
        customer_id=ref[0] if ref else None; method="EXTERNAL_REFERENCE"
        email_matches=[] if not email else [r[0] for r in self.db.execute("SELECT customer_id FROM crm_customers WHERE email_normalized=? AND merged_into IS NULL",(email,))]
        phone_matches=[] if not phone else [r[0] for r in self.db.execute("SELECT customer_id FROM crm_customers WHERE phone_normalized=? AND merged_into IS NULL",(phone,))]
        if not customer_id and email and phone and len(set(email_matches)&set(phone_matches))==1:
            customer_id=next(iter(set(email_matches)&set(phone_matches))); method="EMAIL_AND_PHONE"
        first=str(record.get("first_name") or "").strip() or None; last=str(record.get("last_name") or "").strip() or None
        display=str(record.get("display_name") or " ".join(x for x in (first,last) if x)).strip() or None
        quality,details=self._quality(display,email,phone,provider)
        with self.db:
            if not customer_id:
                customer_id=f"customer:{uuid4()}"
                self.db.execute("INSERT INTO crm_customers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(customer_id,display,first,last,email,phone,record.get("birth_date"),record.get("language"),record.get("country"),record.get("city"),record.get("postal_code"),stamp,stamp,"CLIENT_PARTIAL" if not (email or phone) else "CLIENT_IDENTIFIED",provider,quality,json.dumps(details),None,None,None))
            else:
                self.db.execute("UPDATE crm_customers SET display_name=COALESCE(?,display_name),first_name=COALESCE(?,first_name),last_name=COALESCE(?,last_name),email_normalized=COALESCE(?,email_normalized),phone_normalized=COALESCE(?,phone_normalized),updated_at=? WHERE customer_id=?",(display,first,last,email,phone,stamp,customer_id))
            fingerprint=hashlib.sha256(json.dumps({"email":email,"phone":phone},sort_keys=True).encode()).hexdigest() if email or phone else None
            self.db.execute("INSERT INTO crm_external_references VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,external_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,raw_identity_fingerprint=excluded.raw_identity_fingerprint",(f"ref:{uuid4()}",customer_id,provider,external_id,stamp,stamp,fingerprint,1.0 if method=="EXTERNAL_REFERENCE" else .99,method,0))
            # Exact single-field agreement is a review candidate, never an auto-merge.
            candidates=(set(email_matches)|set(phone_matches))-{customer_id}
            for candidate in candidates:
                evidence={"email_exact":candidate in email_matches,"phone_exact":candidate in phone_matches,"name_ignored":True}
                self.db.execute("INSERT INTO crm_identities VALUES(?,?,?,?,?,?,NULL)",(f"identity:{uuid4()}",customer_id,candidate,"POSSIBLE",json.dumps(evidence),stamp))
            self._source_consents(customer_id,provider,record,stamp)
        return self.customer(customer_id,reveal_pii=True)

    @staticmethod
    def _quality(name,email,phone,source):
        details={"valid_email":bool(email),"valid_phone":bool(phone),"has_name":bool(name),"has_source":bool(source)}
        count=sum(details.values()); return ("COMPLETE" if count==4 else "PARTIAL" if count>=2 else "LOW_QUALITY"),details

    def _source_consents(self,cid,provider,record,stamp):
        # PrestaShop flags are purpose/channel-specific evidence, not universal consent.
        for field,purpose in (("newsletter","newsletter"),("optin","partners")):
            if field in record:
                status="GRANTED" if record[field] in (True,1,"1") else "DENIED"
                self.record_consent(cid,"EMAIL",status,provider,stamp,f"source field: {field}",purpose)

    def record_consent(self,customer_id,channel,status,source,observed_at=None,evidence=None,purpose="marketing",policy_version=None,expires_at=None):
        channel=channel.upper();status=status.upper()
        if channel not in CONSENT_CHANNELS or status not in CONSENT_STATES: raise ValueError("invalid consent")
        if status=="GRANTED" and not evidence: raise ValueError("GRANTED requires explicit evidence")
        cid=f"consent:{uuid4()}"; stamp=now()
        with self.db:self.db.execute("INSERT INTO crm_consents VALUES(?,?,?,?,?,?,?,?,?,?,?)",(cid,customer_id,channel,status,source,observed_at or stamp,evidence,purpose,policy_version,expires_at,stamp))
        return cid

    def consent_status(self,customer_id,channel,purpose=None):
        if purpose is None:
            row=self.db.execute("SELECT status FROM crm_consents WHERE customer_id=? AND channel=? ORDER BY observed_at DESC,created_at DESC LIMIT 1",(customer_id,channel)).fetchone()
        else:
            row=self.db.execute("SELECT status FROM crm_consents WHERE customer_id=? AND channel=? AND purpose=? ORDER BY observed_at DESC,created_at DESC LIMIT 1",(customer_id,channel,purpose)).fetchone()
        return row[0] if row else "UNKNOWN"

    def link_sale(self,customer_id,sale_event_id,*,source,link_method="EXTERNAL_CUSTOMER_ID",confidence=1.0,manually_verified=False):
        if link_method not in {"EXTERNAL_CUSTOMER_ID","EXTERNAL_REFERENCE","ORDER_REFERENCE","MANUAL_VERIFIED"}: raise ValueError("non-deterministic sale link")
        with self.db:self.db.execute("INSERT OR IGNORE INTO crm_sale_links VALUES(?,?,?,?,?,?,?,?)",(f"link:{uuid4()}",customer_id,sale_event_id,link_method,float(confidence),source,now(),int(manually_verified)))

    def refresh_metrics(self,customer_id=None,period_end=None):
        """Aggregate linked sale_events only: this is a projection, never a sales copy."""
        end=period_end or now(); ids=[customer_id] if customer_id else [r[0] for r in self.db.execute("SELECT customer_id FROM crm_customers WHERE merged_into IS NULL")]
        count=0
        for cid in ids:
            rows=self.db.execute("SELECT s.* FROM sale_events s JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id WHERE l.customer_id=? AND s.sold_at<=? ORDER BY s.sold_at",(cid,end)).fetchall()
            if not rows: continue
            sales=[r for r in rows if r["event_kind"]=="SALE"]; refunds=[r for r in rows if r["event_kind"] in {"REFUND","RETURN"}]
            money=lambda rs,col: sum((Decimal(r[col]) for r in rs if r[col] is not None),Decimal("0"))
            revenue=money(sales,"line_total_ttc")-money(refunds,"line_total_ttc"); orders=len({r["external_sale_id"] for r in sales}); dates=[datetime.fromisoformat(r["sold_at"].replace("Z","+00:00")) for r in sales]
            intervals=[(b-a).total_seconds()/86400 for a,b in zip(dates,dates[1:])]; channels={r["channel"] for r in sales if r["channel"]}
            recency=(datetime.fromisoformat(end.replace("Z","+00:00"))-dates[-1]).days if dates else None
            rscore=5 if recency is not None and recency<=30 else 4 if recency is not None and recency<=60 else 3 if recency is not None and recency<=90 else 2 if recency is not None and recency<=180 else 1
            fscore=5 if orders>=10 else 4 if orders>=6 else 3 if orders>=3 else 2 if orders>=2 else 1
            mscore=5 if revenue>=1000 else 4 if revenue>=500 else 3 if revenue>=200 else 2 if revenue>=50 else 1
            segment="Champions" if min(rscore,fscore,mscore)>=4 else "Nouveaux clients" if orders==1 and rscore>=4 else "Inactifs" if rscore<=2 else "Fidèles" if fscore>=3 else "Données insuffisantes"
            rfm={"recency":{"value_days":recency,"score":rscore,"rule":"configurable defaults v1"},"frequency":{"value":orders,"score":fscore,"rule":"orders"},"monetary":{"value":str(revenue),"score":mscore,"rule":"Sales Ledger TTC"},"period_end":end,"segment":segment}
            values=(f"snapshot:{uuid4()}",cid,dates[0].isoformat() if dates else None,end,dates[0].isoformat() if dates else None,dates[-1].isoformat() if dates else None,orders,len({r['external_sale_id'] for r in rows}),str(revenue),str(money(sales,"line_total_ht")-money(refunds,"line_total_ht")),str(revenue/orders) if orders else None,orders/max(1,(dates[-1]-dates[0]).days)*30 if len(dates)>1 else None,sum(intervals)/len(intervals) if intervals else None,str(sum((Decimal(r['quantity']) for r in sales),Decimal('0'))),str(money(refunds,"line_total_ttc")),sum(1 for r in rows if r['event_kind']=="CANCELLATION"),next(iter(channels)) if len(channels)==1 else "OMNICHANNEL" if channels else None,next((r['location'] for r in sales if r['location']),None),json.dumps(rfm),now())
            with self.db:self.db.execute("INSERT OR REPLACE INTO crm_metric_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
            count+=1
        return {"customers_calculated":count,"revenue_authority":"SALES_LEDGER"}

    def loyalty_simulate(self,customer_id,amount,*,points_per_euro=1,minimum=0,maximum=None):
        raw=(Decimal(str(amount))*Decimal(str(points_per_euro))).quantize(Decimal("1"),rounding=ROUND_FLOOR); points=int(raw)
        if Decimal(str(amount))<Decimal(str(minimum)):points=0
        if maximum is not None:points=min(points,int(maximum))
        return {"customer_id":customer_id,"points":points,"simulation":True,"writes_external":False}

    def loyalty_transaction(self,customer_id,kind,points,idempotency_key,reason,actor="system",source_reference=None,reverses=None,expires_at=None):
        if kind not in {"EARN","REDEEM","ADJUST","EXPIRE","CANCEL","REFUND"}:raise ValueError("invalid loyalty kind")
        row=self.db.execute("SELECT account_id FROM loyalty_accounts WHERE customer_id=?",(customer_id,)).fetchone(); stamp=now()
        with self.db:
            if not row:
                aid=f"loyalty:{uuid4()}";self.db.execute("INSERT INTO loyalty_accounts VALUES(?,?,?,?,?,?)",(aid,customer_id,"Standard",1,stamp,stamp))
            else:aid=row[0]
            signed=abs(int(points)) if kind in {"EARN","ADJUST"} else -abs(int(points))
            self.db.execute("INSERT OR IGNORE INTO loyalty_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?)",(f"points:{uuid4()}",aid,kind,signed,source_reference,idempotency_key,stamp,expires_at,reverses,reason,actor))
        return self.loyalty(customer_id)

    def loyalty(self,customer_id):
        row=self.db.execute("SELECT * FROM loyalty_accounts WHERE customer_id=?",(customer_id,)).fetchone()
        if not row:return {"customer_id":customer_id,"balance":None,"status":"NOT_ENROLLED","simulation":True}
        balance=self.db.execute("SELECT COALESCE(sum(points),0) FROM loyalty_transactions WHERE account_id=?",(row["account_id"],)).fetchone()[0]
        return {"customer_id":customer_id,"account_id":row["account_id"],"balance":balance,"tier":row["tier"],"simulation":bool(row["simulation"])}

    def create_campaign(self,name,segment_id,objective,content,channel,consent_required=True):
        channel=channel.upper(); status="DRAFT"
        if channel not in CONSENT_CHANNELS: raise ValueError("invalid channel")
        cid=f"campaign:{uuid4()}";stamp=now()
        with self.db:self.db.execute("INSERT INTO crm_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,name,segment_id,objective,content,channel,int(consent_required),status,None,"{}",stamp,stamp))
        return cid

    def review_campaign(self,campaign_id,customer_ids: Iterable[str]):
        campaign=self.db.execute("SELECT * FROM crm_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
        if not campaign:raise KeyError(campaign_id)
        blocked=[cid for cid in customer_ids if campaign["consent_required"] and self.consent_status(cid,campaign["planned_channel"])!="GRANTED"]
        status="BLOCKED_CONSENT" if blocked else "READY_FOR_REVIEW"
        with self.db:self.db.execute("UPDATE crm_campaigns SET status=?,updated_at=? WHERE campaign_id=?",(status,now(),campaign_id))
        return {"campaign_id":campaign_id,"status":status,"blocked_customers":len(blocked),"external_send":False,"human_review_required":True}

    def customer(self,customer_id,reveal_pii=False):
        row=self.db.execute("SELECT * FROM crm_customers WHERE customer_id=?",(customer_id,)).fetchone()
        if not row:raise KeyError(customer_id)
        result=dict(row);result["quality_details"]=json.loads(result.pop("quality_details_json"))
        if not reveal_pii:result.update(email_normalized=mask_email(result["email_normalized"]),phone_normalized=mask_phone(result["phone_normalized"]))
        result["consents"]={channel:self.consent_status(customer_id,channel) for channel in sorted(CONSENT_CHANNELS)};result["loyalty"]=self.loyalty(customer_id)
        return result

    def customers(self,query="",page=1,per_page=25,reveal_pii=False):
        page=max(1,int(page));per_page=min(100,max(1,int(per_page)));needle=f"%{str(query).strip().casefold()}%"
        where="merged_into IS NULL";args=[]
        if query:where+=" AND (lower(COALESCE(display_name,'')) LIKE ? OR lower(COALESCE(email_normalized,'')) LIKE ? OR COALESCE(phone_normalized,'') LIKE ? OR customer_id IN (SELECT customer_id FROM crm_external_references WHERE lower(external_id) LIKE ?))";args=[needle]*4
        total=self.db.execute(f"SELECT count(*) FROM crm_customers WHERE {where}",args).fetchone()[0]
        ids=[r[0] for r in self.db.execute(f"SELECT customer_id FROM crm_customers WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",(*args,per_page,(page-1)*per_page))]
        return {"items":[self.customer(x,reveal_pii) for x in ids],"page":page,"per_page":per_page,"total":total}

    def cockpit(self):
        identified=self.db.execute("SELECT count(*) FROM crm_customers WHERE status='CLIENT_IDENTIFIED' AND merged_into IS NULL").fetchone()[0]
        ledger=self.db.execute("SELECT count(*),sum(CASE WHEN l.link_id IS NULL THEN 1 ELSE 0 END) FROM sale_events s LEFT JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id WHERE s.event_kind='SALE'").fetchone()
        attributed=self.db.execute("SELECT sum(CAST(s.line_total_ttc AS REAL)) FROM sale_events s JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id WHERE s.event_kind='SALE'").fetchone()[0]
        return {"identified_customers":identified,"attributed_revenue_ttc":attributed,"anonymous_sales":ledger[1],"anonymous_share":None if not ledger[0] else ledger[1]/ledger[0],"freshness":now(),"coverage":{"linked_sales":ledger[0]-ledger[1],"total_sales":ledger[0]},"external_messaging":False}
