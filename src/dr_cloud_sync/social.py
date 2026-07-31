"""Safe-by-default social connections, scheduling and publication orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .marketing import ChannelCapability, MarketingRepository, now


CHANNELS = frozenset({"INSTAGRAM", "FACEBOOK", "SNAPCHAT", "TIKTOK"})


class ConnectionStatus(StrEnum):
    DISCONNECTED="DISCONNECTED"; CONFIGURED="CONFIGURED"; CHECKING="CHECKING"
    CONNECTED="CONNECTED"; DEGRADED="DEGRADED"; ERROR="ERROR"; DISABLED="DISABLED"


class ScheduleStatus(StrEnum):
    SCHEDULED="SCHEDULED"; READY="READY"; BLOCKED="BLOCKED"; PROCESSING="PROCESSING"
    PUBLISHED="PUBLISHED"; FAILED="FAILED"; CANCELLED="CANCELLED"


class ComplianceStatus(StrEnum):
    PASS="PASS"; BLOCK="BLOCK"; NEEDS_REVIEW="NEEDS_REVIEW"; UNCONFIGURED="UNCONFIGURED"


def sanitise(value: Any) -> str | None:
    if value is None: return None
    text=str(value)
    text=re.sub(r"(?i)(access_token|refresh_token|api_key|password|authorization)(\s*[:=]\s*)[^\s,;}]+", r"\1\2[REDACTED]", text)
    return text[:1000]


def sanitise_metadata(value: Any) -> Any:
    """Keep useful provider metadata while removing credential-shaped fields."""
    if isinstance(value, Mapping):
        return {str(k): "[REDACTED]" if re.search(r"(?i)token|secret|api.?key|password|authorization",str(k)) else sanitise_metadata(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [sanitise_metadata(item) for item in value]
    return sanitise(value) if isinstance(value,str) else value


class DisabledSecretProvider:
    def get(self, credential_reference: str) -> str:
        raise RuntimeError("secret provider not configured")


class UnconfiguredCompliancePolicy:
    def evaluate(self, proposal: Mapping[str, Any], channel: str) -> Mapping[str, Any]:
        return {"status": ComplianceStatus.UNCONFIGURED.value, "reason": "Règles de conformité DrCloud non configurées"}


class DisabledSocialProvider:
    def check_connection(self, credential: str | None = None) -> Mapping[str, Any]:
        return {"connected": False, "capabilities": [], "error": "Provider social non configuré"}
    def publish(self, payload: Mapping[str, Any], credential: str, idempotency_key: str) -> Any:
        raise RuntimeError("provider social non configuré")


@dataclass(frozen=True)
class PrerequisiteResult:
    publishable: bool
    reasons: tuple[str, ...]


class SocialConnectionService:
    def __init__(self, repository: MarketingRepository, providers: Mapping[str, Any] | None=None, secret_provider: Any=None):
        self.repo=repository; self.providers={k.upper():v for k,v in (providers or {}).items()}; self.secrets=secret_provider or DisabledSecretProvider()

    def configure(self, channel: str, account_id: str, credential_reference: str, actor: str, display_name: str | None=None) -> dict[str, Any]:
        channel=channel.upper()
        if channel not in CHANNELS: raise ValueError("canal non supporté")
        if not account_id.strip() or not credential_reference.strip(): raise ValueError("compte et référence credential requis")
        identifier=f"connection:{uuid4()}"; stamp=now()
        with self.repo.db:
            existing=self.repo.db.execute("SELECT connection_id FROM social_connections WHERE channel=? AND account_id=?",(channel,account_id)).fetchone()
            if existing:
                identifier=existing[0]; self.repo.db.execute("UPDATE social_connections SET display_name=?,status='CONFIGURED',credential_reference=?,last_error=NULL,updated_at=? WHERE connection_id=?",(display_name,credential_reference,stamp,identifier))
            else:
                self.repo.db.execute("INSERT INTO social_connections(connection_id,channel,account_id,display_name,status,credential_reference,capabilities_json,connected_at,last_check_at,last_error,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(identifier,channel,account_id,display_name,"CONFIGURED",credential_reference,"[]",None,None,None,stamp))
            self.repo.audit("SOCIAL_CONNECTION_CHANGED","SocialConnection",identifier,actor,{"channel":channel,"account_id":account_id,"status":"CONFIGURED"})
        return self.get(identifier)

    def get(self, connection_id: str) -> dict[str, Any]:
        row=self.repo.db.execute("SELECT * FROM social_connections WHERE connection_id=?",(connection_id,)).fetchone()
        if not row: raise KeyError(connection_id)
        return self.repo._decoded(dict(row))

    def check_connection(self, connection_id: str, actor: str) -> dict[str, Any]:
        connection=self.get(connection_id); provider=self.providers.get(connection["channel"])
        checked=now()
        try:
            if provider is None:
                result=DisabledSocialProvider().check_connection(); status=ConnectionStatus.DISABLED
            else:
                credential=self.secrets.get(connection["credential_reference"])
                result=provider.check_connection(credential); status=ConnectionStatus.CONNECTED if result.get("connected") else ConnectionStatus.ERROR
            capabilities=sorted({ChannelCapability(x).value for x in result.get("capabilities",[])})
            error=sanitise(result.get("error")); account=result.get("account_id") or connection["account_id"]
            display=result.get("display_name") or connection.get("display_name")
        except Exception as exc:
            status=ConnectionStatus.ERROR; capabilities=[]; error=sanitise(exc)
            if "credential" in locals() and credential: error=error.replace(str(credential),"[REDACTED]")
            account=connection["account_id"]; display=connection.get("display_name")
        with self.repo.db:
            self.repo.db.execute("UPDATE social_connections SET status=?,account_id=?,display_name=?,capabilities_json=?,connected_at=CASE WHEN ?='CONNECTED' THEN COALESCE(connected_at,?) ELSE connected_at END,last_check_at=?,last_error=?,updated_at=? WHERE connection_id=?",(status.value,account,display,json.dumps(capabilities),status.value,checked,checked,error,checked,connection_id))
            self.repo.audit("SOCIAL_CONNECTION_CHECKED","SocialConnection",connection_id,actor,{"channel":connection["channel"],"account_id":account,"status":status.value,"capabilities":capabilities,"error":error})
        return self.get(connection_id)


class MarketingSchedulingService:
    def __init__(self, repository: MarketingRepository, compliance_policy: Any=None):
        self.repo=repository; self.compliance=compliance_policy or UnconfiguredCompliancePolicy()

    def prerequisites(self, proposal_id: str, creative_id: str, channel: str, account_id: str, scheduled_at: str, timezone_name: str) -> PrerequisiteResult:
        reasons=[]; channel=channel.upper()
        proposal=self.repo.db.execute("SELECT * FROM marketing_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        asset=self.repo.db.execute("SELECT * FROM marketing_assets WHERE creative_id=? AND proposal_id=?",(creative_id,proposal_id)).fetchone()
        connection=self.repo.db.execute("SELECT * FROM social_connections WHERE channel=? AND account_id=?",(channel,account_id)).fetchone()
        if not proposal or proposal["status"]!="APPROVED": reasons.append("CONTENT_NOT_APPROVED")
        if not asset or asset["status"]!="APPROVED" or asset["packaging_policy"]!="PRESERVE_ORIGINAL": reasons.append("ASSET_NOT_APPROVED")
        if channel not in CHANNELS: reasons.append("UNSUPPORTED_CHANNEL")
        try:
            ZoneInfo(timezone_name); due=datetime.fromisoformat(scheduled_at)
            if due.tzinfo is None or due.astimezone(timezone.utc)<=datetime.now(timezone.utc): reasons.append("INVALID_DATE")
        except (ValueError, ZoneInfoNotFoundError): reasons.append("INVALID_DATE_OR_TIMEZONE")
        if not connection or connection["status"] not in {"CONNECTED","DEGRADED"}: reasons.append("CONNECTION_UNAVAILABLE")
        if asset and connection:
            capabilities=set(json.loads(connection["capabilities_json"] or "[]")); fmt=asset["format"].upper()
            needed={"STORY":"CAN_PUBLISH_STORY","VIDEO":"CAN_PUBLISH_VIDEO","CAROUSEL":"CAN_PUBLISH_CAROUSEL"}.get(fmt,"CAN_PUBLISH_IMAGE")
            if needed not in capabilities: reasons.append(f"MISSING_CAPABILITY:{needed}")
        if proposal:
            decision=self.compliance.evaluate(dict(proposal),channel)
            if decision.get("status")!=ComplianceStatus.PASS.value: reasons.append(f"COMPLIANCE_{decision.get('status','UNCONFIGURED')}")
        return PrerequisiteResult(not reasons,tuple(dict.fromkeys(reasons)))

    def create(self, proposal_id: str, creative_id: str, channel: str, account_id: str, scheduled_at: str, timezone_name: str, actor: str) -> dict[str, Any]:
        check=self.prerequisites(proposal_id,creative_id,channel,account_id,scheduled_at,timezone_name)
        status=ScheduleStatus.SCHEDULED if check.publishable else ScheduleStatus.BLOCKED
        reason="; ".join(check.reasons) or None; key=self._key(proposal_id,creative_id,channel,scheduled_at,account_id)
        identifier=f"schedule:{uuid4()}"; stamp=now()
        with self.repo.db:
            existing=self.repo.db.execute("SELECT schedule_id FROM marketing_schedules WHERE idempotency_key=?",(key,)).fetchone()
            if existing: return self.get(existing[0])
            self.repo.db.execute("INSERT INTO marketing_schedules(schedule_id,proposal_id,channel,creative_id,scheduled_at,timezone,status,created_at,account_id,blocking_reason,updated_at,attempts,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(identifier,proposal_id,channel.upper(),creative_id,scheduled_at,timezone_name,status.value,stamp,account_id,reason,stamp,0,key))
            self.repo.audit("MARKETING_SCHEDULE_CREATED","MarketingSchedule",identifier,actor,{"proposal_id":proposal_id,"creative_id":creative_id,"channel":channel.upper(),"account_id":account_id,"scheduled_at":scheduled_at,"timezone":timezone_name,"status":status.value,"blocking_reason":reason})
        return self.get(identifier)

    @staticmethod
    def _key(*parts: str) -> str: return hashlib.sha256("|".join((*parts,"v1")).encode()).hexdigest()
    def get(self, schedule_id: str) -> dict[str, Any]:
        row=self.repo.db.execute("SELECT * FROM marketing_schedules WHERE schedule_id=?",(schedule_id,)).fetchone()
        if not row: raise KeyError(schedule_id)
        return dict(row)
    def cancel(self, schedule_id: str, actor: str) -> dict[str, Any]:
        with self.repo.db:
            changed=self.repo.db.execute("UPDATE marketing_schedules SET status='CANCELLED',updated_at=? WHERE schedule_id=? AND status NOT IN ('PUBLISHED','PROCESSING','CANCELLED')",(now(),schedule_id)).rowcount
            if not changed: raise ValueError("programmation non annulable")
            self.repo.audit("MARKETING_SCHEDULE_CANCELLED","MarketingSchedule",schedule_id,actor,{})
        return self.get(schedule_id)

    def update(self, schedule_id: str, scheduled_at: str, timezone_name: str, actor: str) -> dict[str, Any]:
        current=self.get(schedule_id)
        if current["status"] in {"PUBLISHED","PROCESSING","CANCELLED"}: raise ValueError("programmation non modifiable")
        check=self.prerequisites(current["proposal_id"],current["creative_id"],current["channel"],current["account_id"],scheduled_at,timezone_name)
        status="SCHEDULED" if check.publishable else "BLOCKED"; reason="; ".join(check.reasons) or None
        with self.repo.db:
            self.repo.db.execute("UPDATE marketing_schedules SET scheduled_at=?,timezone=?,status=?,blocking_reason=?,updated_at=?,next_retry_at=NULL WHERE schedule_id=?",(scheduled_at,timezone_name,status,reason,now(),schedule_id))
            self.repo.audit("MARKETING_SCHEDULE_UPDATED","MarketingSchedule",schedule_id,actor,{"scheduled_at":scheduled_at,"timezone":timezone_name,"status":status,"blocking_reason":reason})
        return self.get(schedule_id)


class SocialPublishingService:
    MAX_ATTEMPTS=3
    def __init__(self, repository: MarketingRepository, publishers: Mapping[str,Any] | None=None, secret_provider: Any=None, compliance_policy: Any=None):
        self.repo=repository; self.publishers={k.upper():v for k,v in (publishers or {}).items()}; self.secrets=secret_provider or DisabledSecretProvider(); self.scheduling=MarketingSchedulingService(repository,compliance_policy)

    def process_due(self, at: str | None=None, actor: str="worker:marketing-publisher") -> list[dict[str,Any]]:
        stamp=at or now(); ids=[r[0] for r in self.repo.db.execute("SELECT schedule_id FROM marketing_schedules WHERE status IN ('SCHEDULED','READY','FAILED') AND scheduled_at<=? AND (next_retry_at IS NULL OR next_retry_at<=?) ORDER BY scheduled_at",(stamp,stamp))]
        return [result for identifier in ids if (result:=self.process(identifier,actor)) is not None]

    def process(self, schedule_id: str, actor: str="worker:marketing-publisher") -> dict[str,Any] | None:
        prior=self.repo.db.execute("SELECT * FROM social_post_results WHERE schedule_id=? AND status='PUBLISHED'",(schedule_id,)).fetchone()
        if prior: return dict(prior)
        stamp=now()
        with self.repo.db:
            claimed=self.repo.db.execute("UPDATE marketing_schedules SET status='PROCESSING',attempts=attempts+1,last_attempt_at=?,updated_at=? WHERE schedule_id=? AND status IN ('SCHEDULED','READY','FAILED') AND attempts<?",(stamp,stamp,schedule_id,self.MAX_ATTEMPTS)).rowcount
            if not claimed: return None
            self.repo.audit("PUBLICATION_STARTED","MarketingSchedule",schedule_id,actor,{})
        schedule=dict(self.repo.db.execute("SELECT * FROM marketing_schedules WHERE schedule_id=?",(schedule_id,)).fetchone())
        check=self.scheduling.prerequisites(schedule["proposal_id"],schedule["creative_id"],schedule["channel"],schedule["account_id"],(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),schedule["timezone"])
        if not check.publishable: return self._blocked(schedule,"; ".join(check.reasons),actor)
        connection=self.repo.db.execute("SELECT * FROM social_connections WHERE channel=? AND account_id=?",(schedule["channel"],schedule["account_id"])).fetchone()
        publisher=self.publishers.get(schedule["channel"])
        if not publisher: return self._blocked(schedule,"PROVIDER_DISABLED",actor)
        proposal=dict(self.repo.db.execute("SELECT * FROM marketing_proposals WHERE proposal_id=?",(schedule["proposal_id"],)).fetchone()); asset=dict(self.repo.db.execute("SELECT * FROM marketing_assets WHERE creative_id=?",(schedule["creative_id"],)).fetchone())
        try:
            credential=self.secrets.get(connection["credential_reference"])
            output=publisher.publish({"proposal":proposal,"asset":asset,"channel":schedule["channel"],"account_id":schedule["account_id"]},credential,schedule["idempotency_key"])
            external=sanitise(output.get("external_post_id") if isinstance(output,Mapping) else str(output)); metadata=sanitise_metadata(output.get("metadata",{}) if isinstance(output,Mapping) else {})
            if not external: raise RuntimeError("provider success without external post id")
            post_id=f"post:{uuid4()}"; published=now()
            with self.repo.db:
                self.repo.db.execute("INSERT INTO social_post_results(post_id,proposal_id,creative_id,schedule_id,channel,account_id,published_at,status,raw_json,provider_metadata_json,error,external_post_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(post_id,schedule["proposal_id"],schedule["creative_id"],schedule_id,schedule["channel"],schedule["account_id"],published,"PUBLISHED","{}",json.dumps(metadata),None,external,schedule["idempotency_key"]))
                self.repo.db.execute("UPDATE marketing_schedules SET status='PUBLISHED',blocking_reason=NULL,last_error=NULL,updated_at=? WHERE schedule_id=?",(published,schedule_id))
                for product in self.repo.db.execute("SELECT product_key FROM marketing_proposal_products WHERE proposal_id=?",(schedule["proposal_id"],)):
                    self.repo.db.execute("INSERT INTO marketing_publication_history VALUES(?,?,?,?,?,?)",(f"history:{uuid4()}",schedule["proposal_id"],product[0],schedule["creative_id"],schedule["channel"],published))
                self.repo.audit("PUBLICATION_SUCCEEDED","MarketingSchedule",schedule_id,actor,{"post_id":post_id,"external_post_id":external,"channel":schedule["channel"],"account_id":schedule["account_id"]})
            return dict(self.repo.db.execute("SELECT * FROM social_post_results WHERE post_id=?",(post_id,)).fetchone())
        except Exception as exc:
            error=sanitise(exc); attempts=schedule["attempts"]
            if "credential" in locals() and credential: error=error.replace(str(credential),"[REDACTED]")
            retry=(datetime.now(timezone.utc)+timedelta(minutes=5*attempts)).isoformat() if attempts<self.MAX_ATTEMPTS else None
            with self.repo.db:
                self.repo.db.execute("UPDATE marketing_schedules SET status='FAILED',last_error=?,next_retry_at=?,updated_at=? WHERE schedule_id=?",(error,retry,now(),schedule_id))
                self.repo.audit("PUBLICATION_FAILED","MarketingSchedule",schedule_id,actor,{"error":error,"attempts":attempts,"next_retry_at":retry})
            return self.scheduling.get(schedule_id)

    def _blocked(self,schedule: Mapping[str,Any],reason: str,actor: str) -> dict[str,Any]:
        with self.repo.db:
            self.repo.db.execute("UPDATE marketing_schedules SET status='BLOCKED',blocking_reason=?,updated_at=? WHERE schedule_id=?",(sanitise(reason),now(),schedule["schedule_id"]))
            self.repo.audit("PUBLICATION_BLOCKED","MarketingSchedule",schedule["schedule_id"],actor,{"reason":sanitise(reason)})
        return self.scheduling.get(schedule["schedule_id"])
