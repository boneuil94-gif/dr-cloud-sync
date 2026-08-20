import sqlite3

import pytest

from dr_cloud_sync.crm import CRMService
from dr_cloud_sync.crm_privacy import CRMPrivacyError, CRMPrivacyService


def _customer(tmp_path):
    crm = CRMService(tmp_path / "crm.db")
    customer = crm.ingest_customer(
        "PRESTASHOP",
        "customer@example.com",
        {
            "first_name": "Alice",
            "last_name": "Martin",
            "email": "alice@example.com",
            "phone": "0612345678",
            "birth_date": "1990-01-01",
            "city": "Paris",
            "postal_code": "75001",
            "newsletter": True,
        },
    )
    cid = customer["customer_id"]
    stamp = customer["created_at"]
    with crm.db:
        crm.db.execute(
            "INSERT INTO crm_addresses VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("address:1", cid, "PRESTASHOP", "addr-1", "1 rue Exemple", None, "Paris", "75001", "FR", stamp, stamp),
        )
        crm.db.execute(
            "INSERT INTO crm_tags VALUES(?,?,?,?,?,?,?)",
            ("tag:1", cid, "alice-vip", 0, stamp, "test", None),
        )
        crm.db.execute(
            "INSERT INTO crm_activities VALUES(?,?,?,?,?,?,?,?)",
            ("activity:1", cid, "NOTE", stamp, "test", "tester", "ok", '{"email":"alice@example.com"}'),
        )
        crm.db.execute(
            "INSERT INTO crm_interactions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("interaction:1", cid, "NOTE", "Call Alice at 06 12 34 56 78", "staff@example.com", "PRIVATE", "OPEN", stamp, stamp, stamp, None),
        )
        crm.db.execute(
            "INSERT INTO crm_merge_history VALUES(?,?,?,?,?,?,?,?)",
            ("merge:1", cid, cid, '{"email":"alice@example.com"}', stamp, "tester", None, None),
        )
    return crm, cid


def test_retention_policy_is_not_invented(tmp_path):
    crm, cid = _customer(tmp_path)
    privacy = CRMPrivacyService(crm.db)
    policy = privacy.retention_policy()
    assert policy["status"] == "NOT_CONFIGURED"
    assert policy["retention_days"] is None
    assert policy["automatic_deletion"] is False
    assert privacy.status(cid)["last_privacy_action"] is None


def test_anonymise_removes_direct_and_free_text_pii_without_provider_write(tmp_path):
    crm, cid = _customer(tmp_path)
    privacy = CRMPrivacyService(crm.db)
    result = privacy.anonymise(cid, actor="privacy-admin", reason="verified erasure request")
    assert result["result"] == "ANONYMISED"
    assert result["direct_identifier_fields_present"] == 0
    assert result["identity_fingerprints_present"] == 0
    assert result["addresses_present"] == 0
    assert result["nonempty_interactions_present"] == 0
    assert result["external_provider_write"] is False
    row = crm.db.execute("SELECT * FROM crm_customers WHERE customer_id=?", (cid,)).fetchone()
    assert row["status"] == "ANONYMISED" and row["anonymised_at"] is not None
    assert all(row[name] is None for name in (
        "display_name", "first_name", "last_name", "email_normalized", "phone_normalized",
        "birth_date", "language", "country", "city", "postal_code",
    ))
    ref = crm.db.execute("SELECT external_id,raw_identity_fingerprint FROM crm_external_references WHERE customer_id=?", (cid,)).fetchone()
    assert ref["external_id"].startswith("anonymised:") and ref["raw_identity_fingerprint"] is None
    assert crm.db.execute("SELECT count(*) FROM crm_tags WHERE customer_id=?", (cid,)).fetchone()[0] == 0
    assert crm.db.execute("SELECT evidence FROM crm_consents WHERE customer_id=?", (cid,)).fetchone()[0] is None
    assert crm.db.execute("SELECT metadata_json FROM crm_activities WHERE customer_id=?", (cid,)).fetchone()[0] == "{}"
    interaction = crm.db.execute("SELECT content,author,deleted_at FROM crm_interactions WHERE customer_id=?", (cid,)).fetchone()
    assert interaction["content"] == "" and interaction["author"] == "privacy-redacted" and interaction["deleted_at"]
    assert crm.db.execute("SELECT snapshot_json FROM crm_merge_history WHERE merge_id='merge:1'").fetchone()[0] == '{"privacy":"REDACTED"}'
    event = privacy.events(cid)[0]
    assert event["result"] == "COMPLETED" and event["evidence"]["sales_links_preserved"] is True


def test_anonymise_is_idempotent_and_does_not_fabricate_second_event(tmp_path):
    crm, cid = _customer(tmp_path)
    privacy = CRMPrivacyService(crm.db)
    first = privacy.anonymise(cid, actor="privacy-admin", reason="request")
    second = privacy.anonymise(cid, actor="privacy-admin", reason="retry")
    assert first["result"] == "ANONYMISED"
    assert second["result"] == "ALREADY_ANONYMISED"
    assert len(privacy.events(cid)) == 1


def test_anonymise_requires_explicit_actor_and_reason(tmp_path):
    crm, cid = _customer(tmp_path)
    privacy = CRMPrivacyService(crm.db)
    with pytest.raises(ValueError):
        privacy.anonymise(cid, actor="", reason="request")
    with pytest.raises(ValueError):
        privacy.anonymise(cid, actor="privacy-admin", reason="")
    assert privacy.status(cid)["anonymised_at"] is None


def test_privacy_event_ledger_is_append_only(tmp_path):
    crm, cid = _customer(tmp_path)
    privacy = CRMPrivacyService(crm.db)
    privacy.anonymise(cid, actor="privacy-admin", reason="request")
    event_id = privacy.events(cid)[0]["event_id"]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with crm.db:
            crm.db.execute("UPDATE crm_privacy_events SET result='CHANGED' WHERE event_id=?", (event_id,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with crm.db:
            crm.db.execute("DELETE FROM crm_privacy_events WHERE event_id=?", (event_id,))


def test_transaction_failure_rolls_back_customer_anonymisation(tmp_path):
    crm, cid = _customer(tmp_path)
    privacy = CRMPrivacyService(crm.db)
    with crm.db:
        crm.db.execute("CREATE TRIGGER fail_privacy BEFORE DELETE ON crm_addresses BEGIN SELECT RAISE(ABORT,'forced failure'); END")
    with pytest.raises(CRMPrivacyError, match="no partial result"):
        privacy.anonymise(cid, actor="privacy-admin", reason="request")
    row = crm.db.execute("SELECT email_normalized,anonymised_at FROM crm_customers WHERE customer_id=?", (cid,)).fetchone()
    assert row["email_normalized"] == "alice@example.com" and row["anonymised_at"] is None
    assert privacy.events(cid) == []
