from datetime import datetime, timedelta, timezone
import json

from dr_cloud_sync.marketing import MarketingRepository
from dr_cloud_sync.social import (MarketingSchedulingService, SocialConnectionService,
                                  SocialPublishingService)


class Secrets:
    def get(self, reference):
        assert reference == "vault:social:test"
        return "access_token=top-secret"


class Connected:
    def check_connection(self, credential):
        return {"connected": True, "account_id": "acct", "display_name": "DrCloud Test",
                "capabilities": ["CAN_PUBLISH_STORY"]}


class Pass:
    def evaluate(self, proposal, channel): return {"status": "PASS"}


class Publisher:
    def __init__(self): self.calls=0
    def publish(self, payload, credential, idempotency_key):
        self.calls+=1
        return {"external_post_id": "external:1", "metadata": {"accepted": True}}


def prepared(tmp_path):
    repo=MarketingRepository(tmp_path/"social.db"); stamp=datetime.now(timezone.utc).isoformat()
    with repo.db:
        repo.db.execute("INSERT INTO marketing_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("proposal:1",None,"Test","Test","Test",50,"APPROVED",'[\"INSTAGRAM\"]','[\"STORY\"]',"h","b","cta","[]","","{}",stamp,stamp,"proposal-key"))
        repo.db.execute("INSERT INTO marketing_proposal_products VALUES(?,?,?)",("proposal:1","product:1",0))
        repo.db.execute("INSERT INTO marketing_assets(creative_id,proposal_id,format,source,status,media_reference,packaging_policy,created_at) VALUES(?,?,?,?,?,?,?,?)",("creative:1","proposal:1","STORY","FAKE","APPROVED","asset:1","PRESERVE_ORIGINAL",stamp))
    connections=SocialConnectionService(repo,{"INSTAGRAM":Connected()},Secrets())
    connection=connections.configure("INSTAGRAM","acct","vault:social:test","admin","Compte")
    return repo,connections,connection


def test_disabled_and_fake_connection_never_exposes_secret(tmp_path):
    repo,connections,connection=prepared(tmp_path)
    checked=connections.check_connection(connection["connection_id"],"admin")
    assert checked["status"] == "CONNECTED" and checked["capabilities"] == ["CAN_PUBLISH_STORY"]
    assert "top-secret" not in json.dumps(repo.rows("social_connections")+repo.rows("marketing_audit"))
    disabled=SocialConnectionService(repo)
    second=disabled.configure("TIKTOK","other","vault:other","admin")
    assert disabled.check_connection(second["connection_id"],"admin")["status"] == "DISABLED"


def test_schedule_fail_closed_then_publish_is_idempotent_and_historic(tmp_path):
    repo,connections,connection=prepared(tmp_path); connections.check_connection(connection["connection_id"],"admin")
    due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    blocked=MarketingSchedulingService(repo).create("proposal:1","creative:1","INSTAGRAM","acct",due,"Europe/Paris","admin")
    assert blocked["status"] == "BLOCKED" and "COMPLIANCE_UNCONFIGURED" in blocked["blocking_reason"]
    scheduling=MarketingSchedulingService(repo,Pass())
    schedule=scheduling.create("proposal:1","creative:1","INSTAGRAM","acct",due,"Europe/Paris","admin")
    # The fail-closed attempt owns the same content snapshot key.
    assert schedule["schedule_id"] == blocked["schedule_id"]
    with repo.db: repo.db.execute("UPDATE marketing_schedules SET status='SCHEDULED',blocking_reason=NULL WHERE schedule_id=?",(schedule["schedule_id"],))
    publisher=Publisher(); service=SocialPublishingService(repo,{"INSTAGRAM":publisher},Secrets(),Pass())
    first=service.process(schedule["schedule_id"]); second=service.process(schedule["schedule_id"])
    assert first["status"] == "PUBLISHED" and second["post_id"] == first["post_id"] and publisher.calls == 1
    assert len(repo.rows("marketing_publication_history")) == 1


def test_claim_and_revalidation_block_unapproved_content(tmp_path):
    repo,connections,connection=prepared(tmp_path); connections.check_connection(connection["connection_id"],"admin")
    due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    schedule=MarketingSchedulingService(repo,Pass()).create("proposal:1","creative:1","INSTAGRAM","acct",due,"UTC","admin")
    with repo.db: repo.db.execute("UPDATE marketing_proposals SET status='REJECTED' WHERE proposal_id='proposal:1'")
    result=SocialPublishingService(repo,{"INSTAGRAM":Publisher()},Secrets(),Pass()).process(schedule["schedule_id"])
    assert result["status"] == "BLOCKED" and "CONTENT_NOT_APPROVED" in result["blocking_reason"]
    assert SocialPublishingService(repo).process(schedule["schedule_id"]) is None
