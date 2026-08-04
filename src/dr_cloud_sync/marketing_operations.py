"""Decision-oriented read model for the provider-neutral marketing cockpit.

This module deliberately composes the existing marketing tables.  It does not own
publishing, credentials, or a second campaign model.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


def _decode(row) -> dict[str, Any]:
    value = dict(row)
    for key in tuple(value):
        if key.endswith("_json"):
            try: value[key[:-5]] = json.loads(value.pop(key) or "null")
            except json.JSONDecodeError: value[key[:-5]] = None
    return value


class MarketingOperationsService:
    """Build small, truthful, paginated views over existing persisted facts."""

    def __init__(self, repository, social_live, intelligence):
        self.repo, self.social_live, self.intelligence = repository, social_live, intelligence

    def cockpit(self, days: int = 30) -> dict[str, Any]:
        days=max(1,min(days,365)); proposals=self.repo.rows("marketing_proposals",limit=500)
        schedules=self.repo.rows("marketing_schedules",limit=500); opportunities=self.repo.rows("marketing_opportunities",limit=500)
        learning=self.intelligence.learning(); social=self.social_live.cockpit(days)
        snapshots=social["charts"]["content_performance"]
        reach=sum(x["reach"] for x in snapshots if x.get("reach") is not None) if any(x.get("reach") is not None for x in snapshots) else None
        engagements=[x["engagement"] for x in snapshots if x.get("engagement") is not None]
        recommendations=[self._recommendation(x) for x in sorted(opportunities,key=self._score,reverse=True) if x.get("status") in {"OPEN","PROPOSED"}][:5]
        latest=max([x.get("fetched_at") for x in snapshots if x.get("fetched_at")]+[x.get("updated_at") for x in proposals if x.get("updated_at")],default=None)
        kpis=[
            self._kpi("Propositions à valider",sum(x["status"]=="READY_FOR_REVIEW" for x in proposals),"review","/marketing/review"),
            self._kpi("Campagnes actives",sum(x["status"] in {"APPROVED","SCHEDULED","PUBLISHED"} for x in proposals),"active","/marketing/campaigns?status=active"),
            self._kpi("Contenus planifiés",sum(x["status"] not in {"CANCELLED","PUBLISHED"} for x in schedules),"calendar","/marketing/calendar"),
            self._kpi("Portée 30 jours",reach,"analytics","/marketing/campaigns?result=measured"),
            self._kpi("Engagement",round(sum(engagements)/len(engagements),2) if engagements else None,"analytics","/marketing/campaigns?result=measured"),
            self._kpi("Produits à pousser",len({p for x in opportunities if x.get("status") in {"OPEN","PROPOSED"} for p in x.get("product_keys",[])}),"opportunity","/marketing/campaigns?priority=high"),
            self._kpi("Marge potentielle",None,"unavailable","/marketing/campaigns?result=margin"),
            self._kpi("Recommandation principale",recommendations[0]["priority"] if recommendations else None,"decision","#recommendations"),
        ]
        return {"status":"ACTION_REQUIRED" if kpis[0]["value"] else "MONITORING","period":f"P{days}D","freshness":latest,"last_sync":social["last_sync"],"provider_status":"NOT_CONFIGURED" if social["connected_accounts"]==0 else social["status"],"counts":{"pending_reviews":kpis[0]["value"],"active_campaigns":kpis[1]["value"],"measured_contents":learning["contents_measured"]},"kpis":kpis,"recommendations":recommendations,"notifications":self.notifications(proposals,schedules,recommendations),"learning":learning}

    @staticmethod
    def _kpi(label,value,state,href):
        return {"label":label,"value":value,"period":"30 jours","state":state,"evolution":None,"freshness":None,"href":href}

    def _recommendation(self, opportunity):
        score=opportunity.get("score_detail") or opportunity.get("score") or {}; components=score.get("components",score) if isinstance(score,dict) else {}
        available={k:v for k,v in components.items() if v is not None}; missing=[k for k,v in components.items() if v is None]
        confidence=min(1,round(len(available)/max(1,len(components)),2))
        kind=opportunity.get("type","OPPORTUNITY"); products=opportunity.get("product_keys",[])
        total=self._score(opportunity)
        return {"id":opportunity["opportunity_id"],"product":products[0] if products else None,"reason":opportunity.get("reason"),"objective":"Transformer un signal vérifié en contenu à valider","channel":"Provider-neutral","format":"Story / Square","priority":"ÉLEVÉE" if total>=70 else "NORMALE","impact_expected":"À mesurer; aucune causalité affirmée","confidence":confidence,"next_action":"Créer ou ouvrir la proposition","explanation":{"signals":[kind],"available_data":available,"missing_data":missing,"rule":kind,"score":{"total":total,"components":components},"limits":["Aucun provider réel homologué","Corrélation uniquement avant mesure"],"confidence":confidence}}

    @staticmethod
    def _score(opportunity):
        value=opportunity.get("score",0)
        return float(value.get("score",0) if isinstance(value,dict) else value or 0)

    @staticmethod
    def notifications(proposals,schedules,recommendations):
        items=[]
        if (count:=sum(x["status"]=="READY_FOR_REVIEW" for x in proposals)): items.append({"type":"PENDING_REVIEW","message":f"{count} proposition(s) à valider","href":"/marketing/review"})
        if (count:=sum(x["status"]=="BLOCKED" for x in schedules)): items.append({"type":"BLOCKED_CONTENT","message":f"{count} contenu(s) planifié(s) bloqué(s)","href":"/marketing/publishing-queue"})
        items.append({"type":"PROVIDER_ABSENT","message":"Provider réel non configuré — publication externe indisponible","href":"/marketing/publishing-queue"})
        if recommendations: items.append({"type":"PRIORITY_OPPORTUNITY","message":recommendations[0]["reason"],"href":"#recommendations"})
        return items

    def calendar(self, query: dict[str,str]) -> dict[str,Any]:
        return self._search("calendar",query)

    def campaigns(self, query: dict[str,str]) -> dict[str,Any]:
        return self._search("campaigns",query)

    def review(self, query: dict[str,str]) -> dict[str,Any]:
        return self._search("review",query)

    def queue(self, query: dict[str,str]) -> dict[str,Any]:
        result=self._search("calendar",query)
        for item in result["items"]:
            item["queue_status"]="SCHEDULED_INTERNAL" if item["status"] not in {"CANCELLED","PUBLISHED","FAILED"} else item["status"]
            if item["status"]=="BLOCKED": item["queue_status"]="BLOCKED_PROVIDER" if "CONNECTION" in (item.get("blocking_reason") or "") else "BLOCKED_COMPLIANCE"
            item["provider"]="NOT_CONFIGURED"; item["account"] = None
        return result

    def _search(self, view, query):
        page=max(1,int(query.get("page","1") or 1)); size=max(1,min(100,int(query.get("page_size","25") or 25))); term=(query.get("q") or "").casefold()
        if view=="calendar":
            rows=[_decode(x) for x in self.repo.db.execute("""SELECT s.*,p.title,p.objective,p.formats_json,p.status AS validation_status
              FROM marketing_schedules s JOIN marketing_proposals p ON p.proposal_id=s.proposal_id ORDER BY s.scheduled_at""")]
        elif view=="review":
            rows=[_decode(x) for x in self.repo.db.execute("SELECT * FROM marketing_proposals WHERE status IN ('DRAFT','READY_FOR_REVIEW','REJECTED') ORDER BY updated_at DESC")]
        else:
            rows=[_decode(x) for x in self.repo.db.execute("""SELECT p.*,h.baseline_json,h.uplift_json,h.confidence,h.outcome,h.attribution,h.social_metrics_json,h.sales_metrics_json,h.measured_at
              FROM marketing_proposals p LEFT JOIN marketing_hypotheses h ON h.proposal_id=p.proposal_id ORDER BY p.updated_at DESC""")]
        status=(query.get("status") or "").upper()
        if term: rows=[x for x in rows if term in json.dumps(x,ensure_ascii=False).casefold()]
        if status and status not in {"ALL","ACTIVE"}: rows=[x for x in rows if str(x.get("status","")).upper()==status]
        if status=="ACTIVE": rows=[x for x in rows if x.get("status") in {"APPROVED","SCHEDULED","PUBLISHED"}]
        total=len(rows); start=(page-1)*size
        return {"items":rows[start:start+size],"page":page,"page_size":size,"total":total,"filters":{k:v for k,v in query.items() if v},"provider_status":"NOT_CONFIGURED","external_publication_enabled":False}
