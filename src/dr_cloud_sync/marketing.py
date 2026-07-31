"""Safe, provider-neutral Marketing Automation foundation for DrCloud OS.

The module deliberately separates deterministic business decisions from SQLite and
future AI/social vendors.  Autopilot is opt-in and publishing is not implemented.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SignalType(StrEnum):
    NEW_PRODUCT="NEW_PRODUCT"; RESTOCK="RESTOCK"; HIGH_STOCK="HIGH_STOCK"
    LOW_ROTATION="LOW_ROTATION"; BEST_SELLER="BEST_SELLER"; TRENDING_PRODUCT="TRENDING_PRODUCT"
    SALES_SPIKE="SALES_SPIKE"; SALES_DROP="SALES_DROP"; MARGIN_OPPORTUNITY="MARGIN_OPPORTUNITY"
    SEASON="SEASON"; WEATHER="WEATHER"; HOLIDAY="HOLIDAY"; LOCAL_EVENT="LOCAL_EVENT"
    STORE_EVENT="STORE_EVENT"; PROMOTION="PROMOTION"; PRODUCT_ACTIVE="PRODUCT_ACTIVE"
    PRODUCT_MEDIA_READY="PRODUCT_MEDIA_READY"; PRODUCT_CREATED="PRODUCT_CREATED"
    MANUAL_SIGNAL="MANUAL_SIGNAL"


class ProposalStatus(StrEnum):
    DRAFT="DRAFT"; READY_FOR_REVIEW="READY_FOR_REVIEW"; APPROVED="APPROVED"
    REJECTED="REJECTED"; SCHEDULED="SCHEDULED"; PUBLISHING="PUBLISHING"
    PUBLISHED="PUBLISHED"; FAILED="FAILED"; ARCHIVED="ARCHIVED"


class ChannelCapability(StrEnum):
    CAN_PUBLISH_IMAGE="CAN_PUBLISH_IMAGE"; CAN_PUBLISH_VIDEO="CAN_PUBLISH_VIDEO"
    CAN_PUBLISH_STORY="CAN_PUBLISH_STORY"; CAN_SCHEDULE="CAN_SCHEDULE"
    CAN_FETCH_ANALYTICS="CAN_FETCH_ANALYTICS"; CAN_PUBLISH_CAROUSEL="CAN_PUBLISH_CAROUSEL"


@dataclass(frozen=True)
class CreativeBrief:
    objective: str; audience: str; angle: str; required_assets: tuple[str, ...]
    packaging_policy: str = "PRESERVE_ORIGINAL"
    style: str = "Identité DrCloud"
    formats: tuple[str, ...] = ("STORY", "SQUARE")
    cta: str = "Disponible chez DrCloud."


@dataclass(frozen=True)
class BrandKit:
    brand_kit_id: str = "brandkit:drcloud"
    logo_asset: str = "/drcloud-logo.png"
    colors: tuple[str, ...] = ("#111111", "#FFFFFF", "#16A366")
    typography_rules: tuple[str, ...] = ("Utiliser uniquement les typographies approuvées",)
    tone: str = "Moderne, premium, très propre et fidèle à DrCloud"
    preferred_ctas: tuple[str, ...] = ("Disponible chez DrCloud.",)
    allowed_elements: tuple[str, ...] = ("assets officiels", "PRIMARY produit original")
    forbidden_elements: tuple[str, ...] = ("logo approximatif", "packaging modifié", "saveur inventée")
    templates: tuple[str, ...] = ("DRCLOUD_CLEAN_PRODUCT",)
    safe_zones: Mapping[str, Any] = field(default_factory=dict)


class CreativeGeneratorPort(Protocol):
    def generate(self, brief: CreativeBrief, product: Any, media: Any,
                 brand_kit: BrandKit, format: str) -> Any: ...


class CopyGeneratorPort(Protocol):
    def generate(self, brief: CreativeBrief, brand_kit: BrandKit) -> Mapping[str, Any]: ...


class SocialPublisherPort(Protocol):
    @property
    def capabilities(self) -> frozenset[ChannelCapability]: ...
    def publish(self, approved_proposal: Mapping[str, Any]) -> Any: ...


class _DisabledSocialAdapter:
    """Capability declaration only; external publication is intentionally absent."""
    channel = ""
    capabilities = frozenset({ChannelCapability.CAN_PUBLISH_IMAGE})
    def publish(self, approved_proposal: Mapping[str, Any]) -> Any:
        raise NotImplementedError(f"{self.channel} publishing is not configured")


class InstagramAdapter(_DisabledSocialAdapter):
    channel="INSTAGRAM"
    capabilities=frozenset({ChannelCapability.CAN_PUBLISH_IMAGE,ChannelCapability.CAN_PUBLISH_STORY,ChannelCapability.CAN_PUBLISH_CAROUSEL})


class FacebookAdapter(_DisabledSocialAdapter): channel="FACEBOOK"
class SnapchatAdapter(_DisabledSocialAdapter):
    channel="SNAPCHAT"; capabilities=frozenset({ChannelCapability.CAN_PUBLISH_IMAGE,ChannelCapability.CAN_PUBLISH_STORY})
class TikTokAdapter(_DisabledSocialAdapter):
    channel="TIKTOK"; capabilities=frozenset({ChannelCapability.CAN_PUBLISH_VIDEO})


class SecretProvider(Protocol):
    def get(self, credential_reference: str) -> str: ...


class CompliancePolicy(Protocol):
    def evaluate(self, proposal: Mapping[str, Any], channel: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class DataValue:
    available: bool
    value: Any = None
    reason: str = "source unavailable"


class SalesMarketingDataPort(Protocol):
    def metrics(self, product_key: str, period: str) -> Mapping[str, DataValue]: ...


class StockMarketingDataPort(Protocol):
    def metrics(self, product_key: str) -> Mapping[str, DataValue]: ...


class UnavailableSalesMarketingData:
    def metrics(self, product_key: str, period: str) -> Mapping[str, DataValue]:
        return {name: DataValue(False, reason="Sales Ledger absent") for name in
                ("units_sold", "revenue", "gross_margin", "sales_velocity", "trend", "last_sale_at", "basket_affinity")}


class UnavailableStockMarketingData:
    def metrics(self, product_key: str) -> Mapping[str, DataValue]:
        return {name: DataValue(False, reason="stock marketing source unavailable") for name in
                ("stock_on_hand", "stock_value", "days_of_cover", "recent_restock", "stock_age", "overstock_score")}


class ConfigurableScoringEngine:
    """Weighted mean over available facts only; missing facts never become zero."""
    def __init__(self, weights: Mapping[str, float] | None = None):
        self.weights = dict(weights or {})

    def score(self, components: Mapping[str, float | None]) -> dict[str, Any]:
        available = {k: max(0.0, min(100.0, float(v))) for k, v in components.items() if v is not None}
        weights = {k: self.weights.get(k, 1.0) for k in available}
        total = round(sum(available[k] * weights[k] for k in available) / sum(weights.values()), 2) if weights else None
        return {"score": total, "components": available, "unavailable": sorted(set(components)-set(available))}


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS marketing_signals(signal_id TEXT PRIMARY KEY,signal_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,detected_at TEXT NOT NULL,expires_at TEXT,confidence REAL NOT NULL,priority INTEGER NOT NULL,source TEXT NOT NULL,metadata_json TEXT NOT NULL,status TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS marketing_opportunities(opportunity_id TEXT PRIMARY KEY,type TEXT NOT NULL,product_keys_json TEXT NOT NULL,signal_ids_json TEXT NOT NULL,score REAL NOT NULL,score_json TEXT NOT NULL,reason TEXT NOT NULL,detected_at TEXT NOT NULL,expires_at TEXT,status TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS marketing_campaigns(campaign_id TEXT PRIMARY KEY,name TEXT NOT NULL,status TEXT NOT NULL,channels_json TEXT NOT NULL,created_at TEXT NOT NULL,archived_at TEXT);
CREATE TABLE IF NOT EXISTS marketing_templates(template_id TEXT PRIMARY KEY,name TEXT NOT NULL,format TEXT NOT NULL,zones_json TEXT NOT NULL,copy_structure_json TEXT NOT NULL,required_assets_json TEXT NOT NULL,allowed_ctas_json TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS marketing_proposals(proposal_id TEXT PRIMARY KEY,opportunity_id TEXT,title TEXT NOT NULL,objective TEXT NOT NULL,reason TEXT NOT NULL,priority INTEGER NOT NULL,status TEXT NOT NULL,channels_json TEXT NOT NULL,formats_json TEXT NOT NULL,headline TEXT NOT NULL,body TEXT NOT NULL,cta TEXT NOT NULL,hashtags_json TEXT NOT NULL,legal_text TEXT NOT NULL,creative_brief_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,FOREIGN KEY(opportunity_id) REFERENCES marketing_opportunities(opportunity_id));
CREATE TABLE IF NOT EXISTS marketing_proposal_products(proposal_id TEXT NOT NULL,product_key TEXT NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(proposal_id,product_key),FOREIGN KEY(proposal_id) REFERENCES marketing_proposals(proposal_id));
CREATE TABLE IF NOT EXISTS marketing_assets(creative_id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,format TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,media_reference TEXT,packaging_policy TEXT NOT NULL DEFAULT 'PRESERVE_ORIGINAL',created_at TEXT NOT NULL,FOREIGN KEY(proposal_id) REFERENCES marketing_proposals(proposal_id));
CREATE TABLE IF NOT EXISTS marketing_schedules(schedule_id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,channel TEXT NOT NULL,creative_id TEXT,scheduled_at TEXT NOT NULL,timezone TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(proposal_id) REFERENCES marketing_proposals(proposal_id));
CREATE TABLE IF NOT EXISTS marketing_automation_rules(rule_id TEXT PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL,trigger TEXT NOT NULL,conditions_json TEXT NOT NULL,action_json TEXT NOT NULL,cooldown_hours INTEGER NOT NULL,requires_approval INTEGER NOT NULL,last_triggered_at TEXT);
CREATE TABLE IF NOT EXISTS marketing_product_settings(product_key TEXT PRIMARY KEY,marketing_enabled INTEGER NOT NULL DEFAULT 1,marketing_priority INTEGER NOT NULL DEFAULT 50,marketing_notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS marketing_settings(id INTEGER PRIMARY KEY CHECK(id=1),automation_enabled INTEGER NOT NULL DEFAULT 0,max_proposals_day INTEGER NOT NULL DEFAULT 3,max_posts_day_channel INTEGER NOT NULL DEFAULT 2,approval_required INTEGER NOT NULL DEFAULT 1,default_channels_json TEXT NOT NULL,default_formats_json TEXT NOT NULL,quiet_days_json TEXT NOT NULL,brand_kit_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS social_connections(connection_id TEXT PRIMARY KEY,channel TEXT NOT NULL,account_id TEXT NOT NULL,status TEXT NOT NULL,credential_reference TEXT NOT NULL,connected_at TEXT,last_check_at TEXT,UNIQUE(channel,account_id));
CREATE TABLE IF NOT EXISTS social_post_results(post_id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,channel TEXT NOT NULL,published_at TEXT NOT NULL,reach INTEGER,impressions INTEGER,views INTEGER,clicks INTEGER,engagement REAL,conversions INTEGER,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS marketing_audit(audit_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,actor TEXT NOT NULL,occurred_at TEXT NOT NULL,details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS marketing_publication_history(history_id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,product_key TEXT NOT NULL,creative_id TEXT,channel TEXT NOT NULL,published_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_marketing_signal_status ON marketing_signals(status,detected_at);
CREATE INDEX IF NOT EXISTS ix_marketing_proposal_status ON marketing_proposals(status,updated_at);
CREATE INDEX IF NOT EXISTS ix_marketing_schedule_date ON marketing_schedules(scheduled_at,channel);
"""


class MarketingRepository:
    def __init__(self, path: Path):
        self.path=Path(path); self.db=sqlite3.connect(path,check_same_thread=False); self.db.row_factory=sqlite3.Row
        with self.db:
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO marketing_settings VALUES(1,0,3,2,1,?,?,?,?)",
                (json.dumps(["INSTAGRAM","FACEBOOK"]),json.dumps(["STORY","SQUARE"]),"[]",json.dumps(asdict(BrandKit()))))
            self.db.execute("INSERT OR IGNORE INTO marketing_automation_rules VALUES(?,?,?,?,?,?,?,?,NULL)",
                ("rule:media-ready-spotlight","Produit actif avec média PRIMARY",1,"PRODUCT_MEDIA_READY",json.dumps({"product_status":"ACTIVE"}),json.dumps({"opportunity":"PRODUCT_SPOTLIGHT"}),168,1))

    def settings(self) -> dict[str, Any]:
        row=dict(self.db.execute("SELECT * FROM marketing_settings WHERE id=1").fetchone())
        for key in ("default_channels_json","default_formats_json","quiet_days_json","brand_kit_json"):
            row[key.removesuffix("_json")]=json.loads(row.pop(key))
        row["automation_enabled"]=bool(row["automation_enabled"]); row["approval_required"]=bool(row["approval_required"])
        return row

    def set_automation(self, enabled: bool, actor: str) -> None:
        with self.db:
            self.db.execute("UPDATE marketing_settings SET automation_enabled=? WHERE id=1",(int(enabled),))
            self.audit("AUTOPILOT_CHANGED","settings","1",actor,{"enabled":enabled})

    def audit(self,event: str,entity_type: str,entity_id: str,actor: str,details: Mapping[str,Any]) -> None:
        self.db.execute("INSERT INTO marketing_audit VALUES(?,?,?,?,?,?,?)",
            (f"audit:{uuid4()}",event,entity_type,entity_id,actor,now(),json.dumps(dict(details),ensure_ascii=False,sort_keys=True)))

    def rows(self, table: str, *, status: str | None=None, limit: int=100) -> list[dict[str,Any]]:
        allowed={"marketing_signals","marketing_opportunities","marketing_proposals","marketing_schedules","marketing_campaigns","marketing_assets","marketing_audit","social_connections","social_post_results"}
        if table not in allowed: raise ValueError("table not allowed")
        query=f"SELECT * FROM {table}"; args=[]
        if status: query+=" WHERE status=?";args.append(status)
        query+=" ORDER BY rowid DESC LIMIT ?";args.append(min(500,max(1,limit)))
        return [self._decoded(dict(r)) for r in self.db.execute(query,args)]

    @staticmethod
    def _decoded(row: dict[str,Any]) -> dict[str,Any]:
        for key in list(row):
            if key.endswith("_json"):
                row[key.removesuffix("_json")]=json.loads(row.pop(key))
        return row


class MarketingAutopilot:
    """Deterministic catalogue → signal → opportunity → proposal orchestrator."""
    def __init__(self, repository: MarketingRepository, catalogue: Any, media_repository: Any,
                 scoring: ConfigurableScoringEngine | None=None):
        self.repo=repository; self.catalogue=catalogue; self.media=media_repository
        self.scoring=scoring or ConfigurableScoringEngine({"media_quality_score":1,"freshness_score":.5,"campaign_fatigue_score":1})

    @staticmethod
    def _key(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def preview(self, limit: int | None=None) -> dict[str,Any]:
        maximum=min(limit or self.repo.settings()["max_proposals_day"],self.repo.settings()["max_proposals_day"])
        candidates=[]
        for product in sorted(self.catalogue.all(),key=lambda p:p.drcloud_product_key):
            if str(product.status) not in {"ACTIVE","ProductStatus.ACTIVE"}: continue
            setting=self.repo.db.execute("SELECT marketing_enabled FROM marketing_product_settings WHERE product_key=?",(product.drcloud_product_key,)).fetchone()
            if setting and not setting[0]: continue
            media=self.media.primary(product.drcloud_product_key)
            if not media: continue
            signal_key=self._key("PRODUCT_MEDIA_READY",product.drcloud_product_key,media.media_id)
            score=self.scoring.score({"media_quality_score":100.0,"freshness_score":None,"campaign_fatigue_score":self._fatigue(product.drcloud_product_key)})
            reason=["Produit actif",f"Média PRIMARY disponible ({media.media_id})","Aucune donnée de vente, stock ou saisonnalité inventée"]
            candidates.append({"product_key":product.drcloud_product_key,"product_name":product.name,"media_id":media.media_id,
                "signal":{"signal_type":"PRODUCT_MEDIA_READY","idempotency_key":signal_key,"reason":reason[1]},
                "opportunity":{"type":"PRODUCT_SPOTLIGHT","score":score["score"],"score_detail":score,"reason":" · ".join(reason)},
                "proposal":{"title":f"Mise en avant — {product.name}","status":"DRAFT","requires_approval":True}})
            if len(candidates)>=maximum: break
        return {"mode":"PREVIEW","mutated":False,"automation_enabled":self.repo.settings()["automation_enabled"],"candidates":candidates,
                "unavailable_sources":["sales","stock marketing","weather","holidays","events"]}

    def _fatigue(self, product_key: str) -> float:
        row=self.repo.db.execute("SELECT MAX(published_at) FROM marketing_publication_history WHERE product_key=?",(product_key,)).fetchone()
        if not row or not row[0]: return 100.0
        age=datetime.now(timezone.utc)-datetime.fromisoformat(row[0])
        return max(0.0,min(100.0,age.total_seconds()/86400*10))

    def run(self, actor: str="job:marketing-daily") -> dict[str,Any]:
        if not self.repo.settings()["automation_enabled"]: raise PermissionError("Marketing Autopilot is OFF; explicit activation required")
        preview=self.preview(); created={"signals":0,"opportunities":0,"proposals":0}
        for item in preview["candidates"]:
            with self.repo.db:
                signal_id=self._create_signal(item,actor,created)
                opportunity_id=self._create_opportunity(item,signal_id,actor,created)
                self._create_proposal(item,opportunity_id,actor,created)
        return {"mode":"APPLY","mutated":True,**created}

    def _create_signal(self,item,actor,created):
        key=item["signal"]["idempotency_key"]; existing=self.repo.db.execute("SELECT signal_id FROM marketing_signals WHERE idempotency_key=?",(key,)).fetchone()
        if existing:return existing[0]
        identifier=f"signal:{uuid4()}"; stamp=now()
        self.repo.db.execute("INSERT INTO marketing_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(identifier,"PRODUCT_MEDIA_READY","PRODUCT",item["product_key"],stamp,None,1.0,50,"CATALOGUE",json.dumps({"media_id":item["media_id"]}),"ACTIVE",key))
        self.repo.audit("SIGNAL_CREATED","MarketingSignal",identifier,actor,{"reason":item["signal"]["reason"],"idempotency_key":key});created["signals"]+=1;return identifier

    def _create_opportunity(self,item,signal_id,actor,created):
        key=self._key("PRODUCT_SPOTLIGHT",signal_id); existing=self.repo.db.execute("SELECT opportunity_id FROM marketing_opportunities WHERE idempotency_key=?",(key,)).fetchone()
        if existing:return existing[0]
        identifier=f"opportunity:{uuid4()}"; stamp=now(); expires=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat()
        score=item["opportunity"]["score"] or 0
        self.repo.db.execute("INSERT INTO marketing_opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?)",(identifier,"PRODUCT_SPOTLIGHT",json.dumps([item["product_key"]]),json.dumps([signal_id]),score,json.dumps(item["opportunity"]["score_detail"]),item["opportunity"]["reason"],stamp,expires,"OPEN",key))
        self.repo.audit("OPPORTUNITY_CREATED","MarketingOpportunity",identifier,actor,{"reason":item["opportunity"]["reason"],"score":score});created["opportunities"]+=1;return identifier

    def _create_proposal(self,item,opportunity_id,actor,created):
        key=self._key("PROPOSAL",opportunity_id); existing=self.repo.db.execute("SELECT proposal_id FROM marketing_proposals WHERE idempotency_key=?",(key,)).fetchone()
        if existing:return existing[0]
        identifier=f"proposal:{uuid4()}";stamp=now(); brief=CreativeBrief("Mise en avant produit","Clients DrCloud","disponibilité produit",(item["media_id"],))
        settings=self.repo.settings(); self.repo.db.execute("INSERT INTO marketing_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(identifier,opportunity_id,item["proposal"]["title"],brief.objective,item["opportunity"]["reason"],50,"DRAFT",json.dumps(settings["default_channels"]),json.dumps(settings["default_formats"]),"","",brief.cta,"[]","",json.dumps(asdict(brief)),stamp,stamp,key))
        self.repo.db.execute("INSERT INTO marketing_proposal_products VALUES(?,?,0)",(identifier,item["product_key"]))
        self.repo.audit("PROPOSAL_CREATED","ContentProposal",identifier,actor,{"status":"DRAFT","requires_approval":True});created["proposals"]+=1;return identifier

    def create_manual_proposal(self,title: str,product_keys: Sequence[str],reason: str,actor: str) -> str:
        if not title.strip() or not reason.strip() or not product_keys: raise ValueError("title, reason and products are required")
        for key in product_keys:
            if not self.catalogue.get(key): raise ValueError(f"unknown product: {key}")
        identifier=f"proposal:{uuid4()}";stamp=now(); brief=CreativeBrief("Campagne manuelle","Clients DrCloud","défini par l’utilisateur",tuple())
        settings=self.repo.settings(); key=self._key("MANUAL",identifier)
        with self.repo.db:
            self.repo.db.execute("INSERT INTO marketing_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(identifier,None,title.strip(),brief.objective,reason.strip(),50,"DRAFT",json.dumps(settings["default_channels"]),json.dumps(settings["default_formats"]),"","",brief.cta,"[]","",json.dumps(asdict(brief)),stamp,stamp,key))
            for position,product_key in enumerate(dict.fromkeys(product_keys)):self.repo.db.execute("INSERT INTO marketing_proposal_products VALUES(?,?,?)",(identifier,product_key,position))
            self.repo.audit("PROPOSAL_CREATED","ContentProposal",identifier,actor,{"manual":True,"products":list(product_keys)})
        return identifier

    def transition(self,proposal_id: str,target: str,actor: str,reason: str="") -> dict[str,Any]:
        target=ProposalStatus(target); row=self.repo.db.execute("SELECT * FROM marketing_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        if not row: raise KeyError(proposal_id)
        current=ProposalStatus(row["status"]); allowed={ProposalStatus.DRAFT:{ProposalStatus.READY_FOR_REVIEW,ProposalStatus.ARCHIVED},ProposalStatus.READY_FOR_REVIEW:{ProposalStatus.APPROVED,ProposalStatus.REJECTED,ProposalStatus.DRAFT},ProposalStatus.APPROVED:{ProposalStatus.SCHEDULED,ProposalStatus.ARCHIVED},ProposalStatus.REJECTED:{ProposalStatus.DRAFT,ProposalStatus.ARCHIVED},ProposalStatus.SCHEDULED:{ProposalStatus.ARCHIVED}}
        if target not in allowed.get(current,set()): raise ValueError(f"transition {current} -> {target} forbidden")
        if target==ProposalStatus.REJECTED and not reason.strip(): raise ValueError("rejection reason required")
        with self.repo.db:
            self.repo.db.execute("UPDATE marketing_proposals SET status=?,updated_at=? WHERE proposal_id=?",(target.value,now(),proposal_id));self.repo.audit("PROPOSAL_STATUS_CHANGED","ContentProposal",proposal_id,actor,{"from":current.value,"to":target.value,"reason":reason})
        return self.repo._decoded(dict(self.repo.db.execute("SELECT * FROM marketing_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()))

    def schedule(self,proposal_id: str,channel: str,scheduled_at: str,timezone_name: str,actor: str,creative_id: str|None=None) -> str:
        row=self.repo.db.execute("SELECT status FROM marketing_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        if not row or row[0]!="APPROVED": raise PermissionError("Only APPROVED proposals can be scheduled")
        datetime.fromisoformat(scheduled_at); identifier=f"schedule:{uuid4()}"
        with self.repo.db:
            self.repo.db.execute("INSERT INTO marketing_schedules VALUES(?,?,?,?,?,?,?,?)",(identifier,proposal_id,channel,creative_id,scheduled_at,timezone_name,"SCHEDULED",now()));self.repo.db.execute("UPDATE marketing_proposals SET status='SCHEDULED',updated_at=? WHERE proposal_id=?",(now(),proposal_id));self.repo.audit("PROPOSAL_SCHEDULED","MarketingSchedule",identifier,actor,{"proposal_id":proposal_id,"channel":channel,"scheduled_at":scheduled_at})
        return identifier

    def expire(self,at: str|None=None) -> int:
        stamp=at or now()
        with self.repo.db:
            rows=self.repo.db.execute("SELECT opportunity_id FROM marketing_opportunities WHERE status='OPEN' AND expires_at IS NOT NULL AND expires_at<=?",(stamp,)).fetchall()
            for row in rows:self.repo.db.execute("UPDATE marketing_opportunities SET status='EXPIRED' WHERE opportunity_id=?",(row[0],));self.repo.audit("OPPORTUNITY_EXPIRED","MarketingOpportunity",row[0],"job:marketing-expiration",{"at":stamp})
        return len(rows)


class MarketingJobOperations:
    """Names and callables ready to be registered in the existing JobRunner."""
    SIGNAL_DETECTION="MARKETING_SIGNAL_DETECTION"; OPPORTUNITY_GENERATION="MARKETING_OPPORTUNITY_GENERATION"
    PROPOSAL_GENERATION="MARKETING_PROPOSAL_GENERATION"; PUBLISHING="MARKETING_PUBLISHING"
    ANALYTICS_INGESTION="MARKETING_ANALYTICS_INGESTION"
    def __init__(self,autopilot: MarketingAutopilot): self.autopilot=autopilot
    def daily(self) -> Mapping[str,Any]: return self.autopilot.run()
