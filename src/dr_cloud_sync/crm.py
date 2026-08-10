"""Privacy-first CRM and loyalty foundation built on the Sales Ledger.

Customer identity is deliberately separate from commercial events.  The module
only links a sale using an explicit source reference; anonymous ledger rows stay
anonymous and are still included in ledger revenue.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
IDENTITY_STATES={"MATCHED","PROBABLE","AMBIGUOUS","DISTINCT"}
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
CREATE TABLE IF NOT EXISTS crm_rfm_settings(
 settings_id INTEGER PRIMARY KEY CHECK(settings_id=1),config_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_refresh_state(
 state_id INTEGER PRIMARY KEY CHECK(state_id=1),segments_updated_at TEXT,
 recommendations_updated_at TEXT,last_error TEXT);
"""

DEFAULT_RFM_CONFIG={
    "version":1,
    "recency_days":[30,60,90,180],
    "frequency_orders":[1,2,3,6,10],
    "monetary_ttc":[0,50,200,500,1000],
    "lost_after_days":180,
    "reactivate_after_days":90,
    "vip_min_orders":6,
    "vip_min_revenue_ttc":500,
}

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
        with self.db:
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO crm_rfm_settings VALUES(1,?,?,?)",(json.dumps(DEFAULT_RFM_CONFIG),now(),"system-default"))
            self.db.execute("INSERT OR IGNORE INTO crm_refresh_state(state_id) VALUES(1)")

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
                # A single exact contact is strong evidence, but never sufficient
                # for an automatic merge.  A human must resolve this candidate.
                self.db.execute("INSERT INTO crm_identities VALUES(?,?,?,?,?,?,NULL)",(f"identity:{uuid4()}",customer_id,candidate,"PROBABLE",json.dumps(evidence),stamp))
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

    def marketing_consent(self,customer_id,channel="EMAIL"):
        """Return an explicit marketing decision; absence can never become opt-in."""
        row=self.db.execute("""SELECT status,source,observed_at,created_at FROM crm_consents
            WHERE customer_id=? AND channel=? AND purpose IN ('marketing','newsletter')
            ORDER BY observed_at DESC,created_at DESC LIMIT 1""",(customer_id,channel)).fetchone()
        if not row:return {"marketing_consent":"UNKNOWN","consent_source":None,"consent_at":None,"revoked_at":None}
        state={"GRANTED":"OPT_IN","DENIED":"OPT_OUT","WITHDRAWN":"REVOKED"}.get(row["status"],row["status"])
        return {"marketing_consent":state,"consent_source":row["source"],"consent_at":row["observed_at"] if state=="OPT_IN" else None,"revoked_at":row["observed_at"] if state=="REVOKED" else None}

    def rfm_config(self):
        row=self.db.execute("SELECT config_json,updated_at,updated_by FROM crm_rfm_settings WHERE settings_id=1").fetchone()
        return {**json.loads(row[0]),"updated_at":row[1],"updated_by":row[2]}

    def configure_rfm(self,config: Mapping[str,Any],actor="administrator"):
        merged={**DEFAULT_RFM_CONFIG,**dict(config)}
        for key in ("recency_days","frequency_orders","monetary_ttc"):
            values=merged[key]
            if len(values) not in (4,5) or list(values)!=sorted(values):raise ValueError(f"invalid {key}")
        with self.db:self.db.execute("UPDATE crm_rfm_settings SET config_json=?,updated_at=?,updated_by=? WHERE settings_id=1",(json.dumps(merged),now(),actor))
        return self.rfm_config()

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
            money=lambda rs,col: None if any(r[col] is None for r in rs) else sum((Decimal(r[col]) for r in rs),Decimal("0"))
            sales_ttc,refunds_ttc=money(sales,"line_total_ttc"),money(refunds,"line_total_ttc")
            revenue=None if sales_ttc is None or refunds_ttc is None else sales_ttc-refunds_ttc; orders=len({(r["source"],r["external_sale_id"]) for r in sales})
            order_dates={ (r["source"],r["external_sale_id"]):datetime.fromisoformat(r["sold_at"].replace("Z","+00:00")) for r in sales }
            dates=sorted(order_dates.values()); config=self.rfm_config()
            intervals=[(b-a).total_seconds()/86400 for a,b in zip(dates,dates[1:])]; channels={r["channel"] for r in sales if r["channel"]}
            recency=(datetime.fromisoformat(end.replace("Z","+00:00"))-dates[-1]).days if dates else None
            rlimits=config["recency_days"]; rscore=next((5-i for i,x in enumerate(rlimits) if recency<=x),1)
            fscore=sum(orders>=x for x in config["frequency_orders"]); mscore=None if revenue is None else sum(revenue>=Decimal(str(x)) for x in config["monetary_ttc"])
            segment=self._segment(recency,orders,revenue,rscore,fscore,mscore,config)
            rfm={"recency":{"value_days":recency,"score":rscore},"frequency":{"value":orders,"score":fscore},"monetary":{"value":str(revenue) if revenue is not None else None,"score":mscore,"authority":"SALES_LEDGER_TTC"},"config_version":config["version"],"period_end":end,"segment":segment}
            sales_ht,refunds_ht=money(sales,"line_total_ht"),money(refunds,"line_total_ht"); revenue_ht=None if sales_ht is None or refunds_ht is None else sales_ht-refunds_ht
            values=(f"snapshot:{uuid4()}",cid,dates[0].isoformat() if dates else None,end,dates[0].isoformat() if dates else None,dates[-1].isoformat() if dates else None,orders,len({r['external_sale_id'] for r in rows}),str(revenue) if revenue is not None else None,str(revenue_ht) if revenue_ht is not None else None,str(revenue/orders) if orders and revenue is not None else None,orders/max(1,(dates[-1]-dates[0]).days)*30 if len(dates)>1 else None,sum(intervals)/len(intervals) if intervals else None,str(sum((Decimal(r['quantity']) for r in sales),Decimal('0'))),str(refunds_ttc) if refunds_ttc is not None else None,sum(1 for r in rows if r['event_kind']=="CANCELLATION"),next(iter(channels)) if len(channels)==1 else "OMNICHANNEL" if channels else None,next((r['location'] for r in sales if r['location']),None),json.dumps(rfm),now())
            with self.db:self.db.execute("INSERT OR REPLACE INTO crm_metric_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
            count+=1
        stamp=now()
        with self.db:self.db.execute("UPDATE crm_refresh_state SET segments_updated_at=?,recommendations_updated_at=?,last_error=NULL WHERE state_id=1",(stamp,stamp))
        return {"customers_calculated":count,"revenue_authority":"SALES_LEDGER","segments_updated_at":stamp}

    @staticmethod
    def _segment(recency,orders,revenue,rscore,fscore,mscore,config):
        if recency is None or orders<1 or revenue is None:return None
        if recency>config["lost_after_days"]:return "PERDU"
        if orders>=config["vip_min_orders"] and revenue>=Decimal(str(config["vip_min_revenue_ttc"])):return "VIP" if recency<=config["reactivate_after_days"] else "À RÉACTIVER"
        if orders==1:return "NOUVEAU" if recency<=config["recency_days"][0] else "OCCASIONNEL"
        if recency>config["reactivate_after_days"]:return "À RÉACTIVER"
        if fscore>=3:return "FIDÈLE"
        if rscore>=4:return "RÉCENT"
        return "OCCASIONNEL"

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
        result["marketing"] = self.marketing_consent(customer_id)
        return result

    def customer_360(self,customer_id,reveal_pii=False):
        """Unified observed view. Missing source facts deliberately remain ``None``."""
        result=self.customer(customer_id,reveal_pii)
        snapshot=self.db.execute("SELECT * FROM crm_metric_snapshots WHERE customer_id=? ORDER BY period_end DESC LIMIT 1",(customer_id,)).fetchone()
        metrics=dict(snapshot) if snapshot else {}
        rfm=json.loads(metrics.get("rfm_json","{}")) if metrics else {}
        rows=self.db.execute("""SELECT s.* FROM sale_events s JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id
            WHERE l.customer_id=? AND s.event_kind='SALE' ORDER BY s.sold_at""",(customer_id,)).fetchall()
        products={}
        order_product={}
        for row in rows:
            key=row["product_key"]
            if key:
                entry=products.setdefault(key,{"product_key":key,"quantity":Decimal("0"),"purchase_dates":[]})
                entry["quantity"]+=Decimal(row["quantity"]);entry["purchase_dates"].append(row["sold_at"])
                order_product.setdefault((row["source"],row["external_sale_id"]),set()).add(key)
        product_list=[]
        for item in products.values():
            dates=sorted({datetime.fromisoformat(x.replace("Z","+00:00")) for x in item.pop("purchase_dates")})
            intervals=[(b-a).total_seconds()/86400 for a,b in zip(dates,dates[1:])]
            mean=sum(intervals)/len(intervals) if intervals else None
            predicted=(dates[-1]+timedelta(days=mean)).isoformat() if mean is not None else None
            product_list.append({**item,"quantity":str(item["quantity"]),"last_purchase_at":dates[-1].isoformat(),"observed_intervals_days":intervals or None,"average_interval_days":mean,"potential_next_purchase_at":predicted,"prediction_status":"PREDICTED" if predicted else "UNKNOWN"})
        pairs={}
        for keys in order_product.values():
            for a in sorted(keys):
                for b in sorted(keys):
                    if a<b:pairs[(a,b)]=pairs.get((a,b),0)+1
        result["metrics"]={
            "first_visit_at":metrics.get("first_order_at"),"last_visit_at":metrics.get("last_order_at"),
            "orders_count":metrics.get("order_count"),"historical_revenue_ttc":metrics.get("revenue_ttc"),
            "historical_margin":self._known_margin(customer_id),"average_basket":metrics.get("average_basket"),
            "purchase_frequency":metrics.get("purchase_frequency"),"average_interval_days":metrics.get("average_interval_days"),
            "days_since_last_purchase":rfm.get("recency",{}).get("value_days"),"segment":rfm.get("segment"),
            "main_channel":metrics.get("main_channel"),"customer_since":result["created_at"],
        }
        result["products"]=sorted(product_list,key=lambda x:Decimal(x["quantity"]),reverse=True)
        result["favorite_categories"]=None # no authoritative category on sale_events
        result["products_bought_together"]=[{"products":list(k),"observed_orders":v} for k,v in sorted(pairs.items(),key=lambda x:-x[1])]
        result["why_null"]={"favorite_categories":"Sales Ledger has no category field"}
        return result

    def _known_margin(self,customer_id):
        rows=self.db.execute("""SELECT s.line_total_ht,s.cost_basis,s.quantity FROM sale_events s
            JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id WHERE l.customer_id=? AND s.event_kind='SALE'""",(customer_id,)).fetchall()
        if not rows or any(r["line_total_ht"] is None or r["cost_basis"] is None for r in rows):return None
        return str(sum((Decimal(r["line_total_ht"])-Decimal(r["cost_basis"])*Decimal(r["quantity"]) for r in rows),Decimal("0")))

    def duplicate_candidates(self):
        rows=self.db.execute("""SELECT i.*,a.display_name customer_name,b.display_name candidate_name
            FROM crm_identities i JOIN crm_customers a ON a.customer_id=i.customer_id
            JOIN crm_customers b ON b.customer_id=i.candidate_customer_id WHERE i.resolved_at IS NULL ORDER BY i.created_at""").fetchall()
        return [{**dict(r),"evidence":json.loads(r["evidence_json"]),"human_validation_required":r["state"] in {"PROBABLE","AMBIGUOUS"}} for r in rows]

    def action_center(self):
        actions=[]
        for row in self.db.execute("SELECT customer_id,rfm_json,average_interval_days,last_order_at FROM crm_metric_snapshots WHERE (customer_id,period_end) IN (SELECT customer_id,max(period_end) FROM crm_metric_snapshots GROUP BY customer_id)"):
            rfm=json.loads(row["rfm_json"]); segment=rfm.get("segment"); recency=rfm.get("recency",{}).get("value_days")
            reason=None; action=None
            if segment=="VIP" and recency is not None and recency>30:reason=f"VIP sans achat depuis {recency} jours";action="Préparer une réactivation"
            elif segment=="À RÉACTIVER":reason=f"Dernier achat observé il y a {recency} jours";action="Préparer une réactivation"
            elif segment=="NOUVEAU":reason="Une seule commande récente observée";action="Préparer un parcours de fidélisation"
            elif row["average_interval_days"] is not None and recency is not None and recency>=row["average_interval_days"]*.8:reason=f"Proche de l'intervalle observé de {row['average_interval_days']:.1f} jours";action="Vérifier une opportunité de réachat"
            if reason:
                consent=self.marketing_consent(row["customer_id"])
                actions.append({"customer_id":row["customer_id"],"segment":segment,"why_this_customer":reason,"suggested_action":action,"marketing_consent":consent["marketing_consent"],"send_allowed":consent["marketing_consent"]=="OPT_IN","automatic_send":False})
        return actions

    def customers(self,query="",page=1,per_page=25,reveal_pii=False,filters=None):
        page=max(1,int(page));per_page=min(100,max(1,int(per_page)));needle=f"%{str(query).strip().casefold()}%"
        where="merged_into IS NULL";args=[]
        if query:where+=" AND (lower(COALESCE(display_name,'')) LIKE ? OR lower(COALESCE(email_normalized,'')) LIKE ? OR COALESCE(phone_normalized,'') LIKE ? OR customer_id IN (SELECT customer_id FROM crm_external_references WHERE lower(external_id) LIKE ?))";args=[needle]*4
        total=self.db.execute(f"SELECT count(*) FROM crm_customers WHERE {where}",args).fetchone()[0]
        ids=[r[0] for r in self.db.execute(f"SELECT customer_id FROM crm_customers WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",(*args,per_page,(page-1)*per_page))]
        return {"items":[self.customer(x,reveal_pii) for x in ids],"page":page,"per_page":per_page,"total":total}

    def cockpit(self):
        identified=self.db.execute("SELECT count(*) FROM crm_customers WHERE status='CLIENT_IDENTIFIED' AND merged_into IS NULL").fetchone()[0]
        ledger=self.db.execute("SELECT count(*),sum(CASE WHEN l.link_id IS NULL THEN 1 ELSE 0 END) FROM sale_events s LEFT JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id WHERE s.event_kind='SALE'").fetchone()
        attributed_row=self.db.execute("SELECT sum(CAST(s.line_total_ttc AS REAL)),sum(s.line_total_ttc IS NULL) FROM sale_events s JOIN crm_sale_links l ON l.sale_event_id=s.sale_event_id WHERE s.event_kind='SALE'").fetchone()
        total_row=self.db.execute("SELECT sum(CAST(line_total_ttc AS REAL)),sum(line_total_ttc IS NULL) FROM sale_events WHERE event_kind='SALE'").fetchone()
        attributed=None if attributed_row[1] else attributed_row[0]
        total_revenue=None if total_row[1] else total_row[0]
        snapshots=[json.loads(r[0]) for r in self.db.execute("SELECT rfm_json FROM crm_metric_snapshots WHERE (customer_id,period_end) IN (SELECT customer_id,max(period_end) FROM crm_metric_snapshots GROUP BY customer_id)")]
        distribution={}; active=0
        for rfm in snapshots:
            segment=rfm.get("segment");distribution[segment]=distribution.get(segment,0)+1
            if rfm.get("recency",{}).get("value_days",10**9)<=90:active+=1
        consent_known=self.db.execute("SELECT count(*) FROM crm_customers c WHERE c.merged_into IS NULL AND EXISTS(SELECT 1 FROM crm_consents x WHERE x.customer_id=c.customer_id AND x.purpose IN ('marketing','newsletter'))").fetchone()[0]
        state=self.db.execute("SELECT * FROM crm_refresh_state WHERE state_id=1").fetchone()
        sales_coverage=None if not ledger[0] else (ledger[0]-ledger[1])/ledger[0]
        revenue_coverage=None if total_revenue in (None,0) or attributed is None else attributed/total_revenue
        known=self.db.execute("SELECT count(*) FROM crm_customers WHERE merged_into IS NULL").fetchone()[0]
        probable=self.db.execute("SELECT count(*) FROM crm_identities WHERE state IN ('PROBABLE','AMBIGUOUS') AND resolved_at IS NULL").fetchone()[0]
        return {"identified_customers":identified,"customers_known":known,"customers_active":active if snapshots else None,"attributed_revenue_ttc":attributed,"anonymous_sales":ledger[1],"anonymous_share":None if not ledger[0] else ledger[1]/ledger[0],"freshness":now(),"coverage":{"linked_sales":ledger[0]-ledger[1],"total_sales":ledger[0],"sales_customer_link_coverage":sales_coverage,"revenue_customer_link_coverage":revenue_coverage},"customers_with_marketing_consent":consent_known,"consent_unknown":known-consent_known,"duplicates_probable":probable,"rfm_distribution":distribution,"crm_segments_updated_at":state["segments_updated_at"],"external_messaging":False}
