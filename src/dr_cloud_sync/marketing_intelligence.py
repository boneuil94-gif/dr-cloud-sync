"""Measured, provider-neutral marketing intelligence.

Only persisted facts are surfaced.  Missing inputs remain ``None`` and every
generated proposal still enters the existing human-review workflow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib, json, sqlite3
from typing import Any, Mapping
from uuid import uuid4


class Availability(StrEnum):
    AVAILABLE="AVAILABLE"; PARTIAL="PARTIAL"; NOT_CONFIGURED="NOT_CONFIGURED"
    API_NOT_EXPOSED="API_NOT_EXPOSED"; SCOPE_MISSING="SCOPE_MISSING"; ERROR="ERROR"


class Attribution(StrEnum):
    CORRELATED="CORRELATED"; LIKELY="LIKELY"; UNKNOWN="UNKNOWN"; NO_SIGNAL="NO_SIGNAL"


def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _json(value: Any) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True)


SCHEMA="""
CREATE TABLE IF NOT EXISTS marketing_intelligence_rules(id INTEGER PRIMARY KEY CHECK(id=1),low_stock REAL NOT NULL,overstock REAL NOT NULL,imminent_stockout REAL NOT NULL,dormant_days INTEGER NOT NULL,minimum_stock REAL NOT NULL,target_stock REAL NOT NULL,minimum_margin_percent REAL NOT NULL,floor_price REAL);
INSERT OR IGNORE INTO marketing_intelligence_rules VALUES(1,10,80,3,30,5,40,20,NULL);
CREATE TABLE IF NOT EXISTS marketing_hypotheses(hypothesis_id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,content_id TEXT,product_id TEXT NOT NULL,channel TEXT NOT NULL,format TEXT,audience TEXT,period_json TEXT NOT NULL,social_metrics_json TEXT NOT NULL,sales_metrics_json TEXT NOT NULL,baseline_json TEXT NOT NULL,uplift_json TEXT NOT NULL,confidence REAL,outcome TEXT NOT NULL,attribution TEXT NOT NULL,score_json TEXT NOT NULL,measured_at TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS marketing_alerts(alert_id TEXT PRIMARY KEY,type TEXT NOT NULL,entity_id TEXT NOT NULL,status TEXT NOT NULL,evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE);
"""


class SocialAnalyticsLiveService:
    PROVIDERS=("INSTAGRAM","FACEBOOK","TIKTOK","SNAPCHAT")
    METRICS=("followers","following","publications","reach","impressions","profile_views","clicks","inbound_messages","engagements","engagement_rate")
    def __init__(self, db: sqlite3.Connection, stale_hours: int=24):
        self.db=db; self.stale_hours=stale_hours
        with db: db.execute("""CREATE TABLE IF NOT EXISTS social_analytics_snapshots(
          post_id TEXT PRIMARY KEY,source TEXT NOT NULL,fetched_at TEXT NOT NULL,
          reach INTEGER,impressions INTEGER,views INTEGER,clicks INTEGER,
          engagement REAL,conversions INTEGER,raw_json TEXT NOT NULL)""")

    @staticmethod
    def metric(name: str, value: Any, *, period: str, source: str, fetched_at: str|None, availability: str|None=None, unit: str="COUNT") -> dict[str,Any]:
        state=availability or (Availability.AVAILABLE if value is not None else Availability.API_NOT_EXPOSED)
        return {"name":name,"period":period,"source":source,"freshness":fetched_at,"availability":str(state),"value":value,"unit":unit}

    def cockpit(self, days: int=30) -> dict[str,Any]:
        cutoff=datetime.now(timezone.utc)-timedelta(days=days); connections={r["channel"].upper():dict(r) for r in self.db.execute("SELECT * FROM social_connections")}
        snapshots=[dict(r) for r in self.db.execute("SELECT * FROM social_analytics_snapshots WHERE fetched_at>=? ORDER BY fetched_at",(cutoff.isoformat(),))]
        platforms=[]
        for provider in self.PROVIDERS:
            connection=connections.get(provider); rows=[r for r in snapshots if r["source"].upper()==provider]
            if not connection or connection.get("status")!="CONNECTED": status="NOT_CONFIGURED"; reason="Compte et scopes analytics non configurés"
            elif not rows: status="PARTIAL"; reason="Compte configuré, aucune métrique importée"
            else:
                newest=max(datetime.fromisoformat(r["fetched_at"]) for r in rows);status="STALE" if datetime.now(timezone.utc)-newest>timedelta(hours=self.stale_hours) else "CONNECTED";reason=None
            totals={field:(sum(r[field] for r in rows if r[field] is not None) if any(r[field] is not None for r in rows) else None) for field in ("reach","impressions","views","clicks","conversions")}
            platforms.append({"provider":provider,"status":status,"account":connection.get("display_name") if connection else None,"freshness":max((r["fetched_at"] for r in rows),default=None),"metrics":{k:self.metric(k,v,period=f"P{days}D",source=provider,fetched_at=max((r["fetched_at"] for r in rows),default=None),availability="AVAILABLE" if v is not None else ("NOT_CONFIGURED" if status=="NOT_CONFIGURED" else "API_NOT_EXPOSED")) for k,v in totals.items()},"contents":rows,"api_limits":reason,"next_action":"Configurer le provider" if status=="NOT_CONFIGURED" else "Synchroniser"})
        known=[p for p in platforms if p["status"] in {"CONNECTED","PARTIAL","STALE"}]
        return {"status":"NOT_CONFIGURED" if not known else ("PARTIAL" if any(p["status"]!="CONNECTED" for p in known) else "CONNECTED"),"period":f"P{days}D","last_sync":max((p["freshness"] for p in platforms if p["freshness"]),default=None),"connected_accounts":sum(p["status"]!="NOT_CONFIGURED" for p in platforms),"platforms":platforms,"charts":{"followers":[],"reach_impressions":[],"engagement":[],"content_performance":snapshots},"top_contents":self._tops(snapshots)}

    @staticmethod
    def _tops(rows):
        def top(field, reverse=True): return sorted((r for r in rows if r.get(field) is not None),key=lambda r:r[field],reverse=reverse)[:5]
        return {"views":top("views"),"engagement":top("engagement"),"clicks":top("clicks"),"conversions":top("conversions"),"flop":top("engagement",False)}


class MarketingIntelligenceService:
    def __init__(self, repository, catalogue, stock=None, sales=None, costs=None):
        self.repo=repository;self.catalogue=catalogue;self.stock=stock;self.sales=sales;self.costs=costs
        with self.repo.db: self.repo.db.executescript(SCHEMA)

    def rules(self): return dict(self.repo.db.execute("SELECT * FROM marketing_intelligence_rules WHERE id=1").fetchone())
    def generate(self, actor="job:marketing-intelligence"):
        rules=self.rules();created=[]
        for product in self.catalogue.all():
            key=product.drcloud_product_key; facts={}; quantity=self._quantity(key); sales=self._sales(key); margin=self._margin(key)
            facts.update(stock=quantity,sales_30d=sales,**margin)
            kinds=[]
            if quantity is not None:
                if quantity<=rules["imminent_stockout"]: kinds.append(("IMMINENT_STOCKOUT","Dernières unités",80))
                elif quantity<=rules["low_stock"]: kinds.append(("LOW_STOCK",f"Plus que {quantity:g} disponibles",55))
                if quantity>=rules["overstock"]: kinds.append(("OVERSTOCK","Stock important à écouler",85))
            if quantity and sales==0: kinds.append(("DORMANT_PRODUCT",f"Rotation lente depuis au moins {rules['dormant_days']} jours",75))
            if margin.get("margin_percent") is not None and margin["margin_percent"]>=40 and quantity and quantity>=rules["target_stock"]: kinds.append(("HIGH_MARGIN_LOW_ROTATION","Forte marge et stock disponible",90))
            for kind,reason,priority in kinds:
                created_id=self._proposal(product,key,kind,reason,priority,facts,actor)
                if created_id: created.append(created_id)
        return {"generated":len(created),"proposal_ids":created,"requires_human_review":True}

    def _quantity(self,key):
        try:
            projection=self.stock.position(key) if self.stock else None
            return float(projection.quantity) if projection is not None else None
        except (KeyError,TypeError,AttributeError): return None
    def _sales(self,key):
        try: return float(self.sales.metrics(key,30)["units_sold"])
        except (KeyError,TypeError,AttributeError): return None
    def _margin(self,key):
        try:
            value=next(x for x in self.costs.profitability()["products"] if x["product_key"]==key)
            revenue=float(value["revenue_ht"]); cost=float(value["cost"]); gross=value.get("gross_margin"); units=float(value["units"])
            return {"purchase_cost":cost/units if units else None,"selling_price":revenue/units if units else None,"gross_margin":float(gross) if gross is not None else None,"margin_percent":((revenue-cost)*100/revenue) if gross is not None and revenue else None}
        except (KeyError,TypeError,AttributeError): return {"purchase_cost":None,"selling_price":None,"gross_margin":None,"margin_percent":None}

    def _proposal(self,product,key,kind,reason,priority,facts,actor):
        day=datetime.now(timezone.utc).date().isoformat(); idem=hashlib.sha256(f"intelligence|{kind}|{key}|{day}".encode()).hexdigest()
        if self.repo.db.execute("SELECT 1 FROM marketing_proposals WHERE idempotency_key=?",(idem,)).fetchone(): return None
        score={"sales_score":None if facts["sales_30d"] is None else min(100,facts["sales_30d"]*5),"stock_score":None if facts["stock"] is None else min(100,facts["stock"]),"margin_score":facts["margin_percent"],"social_score":None,"risk_penalty":30 if kind in {"LOW_STOCK","IMMINENT_STOCKOUT"} else 0}
        available=[v for k,v in score.items() if v is not None and k!="risk_penalty"]; total=max(0,round(sum(available)/len(available)-score["risk_penalty"],2)) if available else None
        opportunity=f"opportunity:{uuid4()}";proposal=f"proposal:{uuid4()}";stamp=_now();evidence={k:v for k,v in facts.items() if v is not None}; evidence["availability"]="PARTIAL" if len(evidence)<len(facts) else "AVAILABLE"
        with self.repo.db:
            self.repo.db.execute("INSERT INTO marketing_opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?)",(opportunity,kind,_json([key]),"[]",total or 0,_json({"score":total,"components":score}),reason,stamp,(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(),"PROPOSED",idem+":opportunity"))
            self.repo.db.execute("INSERT INTO marketing_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(proposal,opportunity,f"{reason} — {product.name}","Optimiser stock et marge sans publication automatique",reason,priority,"READY_FOR_REVIEW",_json(["INSTAGRAM"]),_json(["SQUARE"]),reason,f"{product.name} · {reason}","Découvrir en boutique","[]","",_json({"type":kind,"evidence":evidence,"score":{"total":total,"components":score},"discount":None,"guardrail":"Aucune remise chiffrée sans coût et prix plancher vérifiés"}),stamp,stamp,idem))
            self.repo.db.execute("INSERT INTO marketing_proposal_products VALUES(?,?,0)",(proposal,key));self.repo.audit("INTELLIGENCE_PROPOSAL_GENERATED","proposal",proposal,actor,{"type":kind,"evidence":evidence,"score":total})
        return proposal

    def measure(self, proposal_id, content_id, *, product_id, channel, format, published_at, social_metrics: Mapping[str,Any], sales_before, sales_after, tracked_link=False, promo_code=False, actor="job:learning-loop"):
        baseline=None if sales_before is None else float(sales_before); after=None if sales_after is None else float(sales_after);uplift=None if baseline in (None,0) or after is None else (after-baseline)/baseline
        observations=sum(v is not None for v in social_metrics.values())+(baseline is not None)+(after is not None)
        confidence=round(min(1,observations/10 + (.35 if tracked_link or promo_code else 0)),2)
        if after is None or baseline is None: attribution=Attribution.UNKNOWN
        elif after<=baseline: attribution=Attribution.NO_SIGNAL
        elif tracked_link or promo_code: attribution=Attribution.LIKELY
        else: attribution=Attribution.CORRELATED
        score={"social":None if not any(v is not None for v in social_metrics.values()) else social_metrics,"commercial":None if after is None else {"sales":after},"stock":None,"margin":None}
        idem=hashlib.sha256(f"{proposal_id}|{content_id}|{published_at}".encode()).hexdigest();identifier=f"hypothesis:{uuid4()}"
        with self.repo.db:
            cursor=self.repo.db.execute("INSERT OR IGNORE INTO marketing_hypotheses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(identifier,proposal_id,content_id,product_id,channel,format,None,_json({"published_at":published_at,"baseline_days":30}),_json(dict(social_metrics)),_json({"before":baseline,"after":after}),_json({"sales":baseline}),_json({"sales":uplift}),confidence,"POSITIVE" if uplift is not None and uplift>0 else "NO_SIGNAL",attribution.value,_json(score),_now(),idem))
            if cursor.rowcount: self.repo.db.execute("UPDATE marketing_proposals SET status='MEASURED',updated_at=? WHERE proposal_id=? AND status IN ('APPROVED','SCHEDULED','PUBLISHED','MEASURED')",(_now(),proposal_id));self.repo.audit("MARKETING_OUTCOME_MEASURED","hypothesis",identifier,actor,{"attribution":attribution.value,"confidence":confidence})
        return self.learning()

    def learning(self):
        rows=[]
        for row in self.repo.db.execute("SELECT * FROM marketing_hypotheses ORDER BY measured_at DESC"):
            item=dict(row)
            for key in list(item):
                if key.endswith("_json"): item[key[:-5]]=json.loads(item.pop(key))
            rows.append(item)
        uplift=[r["uplift"].get("sales") for r in rows if r["uplift"].get("sales") is not None]
        measured=[r for r in rows if r["confidence"] is not None]
        recommendations=[]
        if len(measured)>=3:
            best=max(measured,key=lambda r:(r["uplift"].get("sales") if r["uplift"].get("sales") is not None else -999))
            recommendations=[{"recommendation":f"Tester davantage {best['channel']} / {best['format']}","observations":len(measured),"period":"historique mesuré","confidence":best["confidence"],"limits":"Corrélation; validation expérimentale requise"}]
        return {"campaigns_measured":len({r["proposal_id"] for r in rows}),"contents_measured":len({r["content_id"] for r in rows if r["content_id"]}),"products":len({r["product_id"] for r in rows}),"average_uplift":sum(uplift)/len(uplift) if uplift else None,"coverage":"EMPTY" if not rows else ("PARTIAL" if any(r["confidence"]<.6 for r in rows) else "MEASURED"),"measurements":rows,"recommendations":recommendations}
