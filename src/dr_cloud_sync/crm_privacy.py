"""Fail-closed local CRM privacy lifecycle.

This module never calls external providers and never guesses a retention period.
It provides an explicit, auditable anonymisation action that removes direct PII
from the local CRM while preserving non-identifying commercial/ledger links.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from uuid import uuid4


EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_privacy_events(
 event_id TEXT PRIMARY KEY,
 customer_id TEXT NOT NULL,
 action TEXT NOT NULL,
 result TEXT NOT NULL,
 actor TEXT NOT NULL,
 reason TEXT NOT NULL,
 occurred_at TEXT NOT NULL,
 evidence_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_crm_privacy_customer ON crm_privacy_events(customer_id,occurred_at DESC);
CREATE TRIGGER IF NOT EXISTS crm_privacy_events_no_update BEFORE UPDATE ON crm_privacy_events
BEGIN SELECT RAISE(ABORT,'privacy event ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS crm_privacy_events_no_delete BEFORE DELETE ON crm_privacy_events
BEGIN SELECT RAISE(ABORT,'privacy event ledger is append-only'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CRMPrivacyError(RuntimeError):
    pass


class CRMPrivacyService:
    """Local-only privacy lifecycle with explicit human-authorised actions.

    No automatic expiry is performed because no legal/business retention duration
    is authoritative in code. A caller must explicitly request anonymisation.
    """

    def __init__(self, path: Path | str | sqlite3.Connection):
        if isinstance(path, sqlite3.Connection):
            self.db = path
            self.path = None
        else:
            self.path = Path(path)
            self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.executescript(EVENT_SCHEMA)

    def retention_policy(self) -> dict:
        return {
            "status": "NOT_CONFIGURED",
            "automatic_deletion": False,
            "retention_days": None,
            "reason": "No authoritative CRM PII retention duration is configured.",
        }

    def status(self, customer_id: str) -> dict:
        row = self.db.execute(
            "SELECT customer_id,status,anonymised_at,deleted_at,"
            "display_name,email_normalized,phone_normalized,birth_date,city,postal_code "
            "FROM crm_customers WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        if not row:
            raise KeyError(customer_id)
        direct_identifiers = sum(
            value is not None and str(value).strip() != ""
            for value in (
                row["display_name"], row["email_normalized"], row["phone_normalized"],
                row["birth_date"], row["city"], row["postal_code"],
            )
        )
        refs = self.db.execute(
            "SELECT count(*) FROM crm_external_references WHERE customer_id=? AND raw_identity_fingerprint IS NOT NULL",
            (customer_id,),
        ).fetchone()[0]
        addresses = self.db.execute(
            "SELECT count(*) FROM crm_addresses WHERE customer_id=?", (customer_id,)
        ).fetchone()[0]
        interactions = self.db.execute(
            "SELECT count(*) FROM crm_interactions WHERE customer_id=? AND length(trim(content))>0",
            (customer_id,),
        ).fetchone()[0]
        last = self.db.execute(
            "SELECT action,result,occurred_at FROM crm_privacy_events WHERE customer_id=? ORDER BY occurred_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
        return {
            "customer_id": customer_id,
            "status": row["status"],
            "anonymised_at": row["anonymised_at"],
            "deleted_at": row["deleted_at"],
            "direct_identifier_fields_present": direct_identifiers,
            "identity_fingerprints_present": refs,
            "addresses_present": addresses,
            "nonempty_interactions_present": interactions,
            "last_privacy_action": dict(last) if last else None,
            "external_provider_write": False,
            "retention_policy": self.retention_policy(),
        }

    def anonymise(self, customer_id: str, *, actor: str, reason: str) -> dict:
        actor = str(actor or "").strip()
        reason = str(reason or "").strip()
        if not actor or not reason:
            raise ValueError("actor and reason are required")
        customer = self.db.execute(
            "SELECT customer_id,anonymised_at FROM crm_customers WHERE customer_id=?", (customer_id,)
        ).fetchone()
        if not customer:
            raise KeyError(customer_id)
        if customer["anonymised_at"]:
            return {**self.status(customer_id), "result": "ALREADY_ANONYMISED"}

        stamp = _now()
        event_id = f"privacy:{uuid4()}"
        tombstone_prefix = f"anonymised:{uuid4()}"
        try:
            with self.db:
                # Direct customer PII.
                self.db.execute(
                    """UPDATE crm_customers SET
                    display_name=NULL,first_name=NULL,last_name=NULL,email_normalized=NULL,
                    phone_normalized=NULL,birth_date=NULL,language=NULL,country=NULL,city=NULL,
                    postal_code=NULL,status='ANONYMISED',data_quality='ANONYMOUS',
                    quality_details_json='{}',anonymised_at=?,updated_at=?
                    WHERE customer_id=?""",
                    (stamp, stamp, customer_id),
                )

                # Provider identifiers/fingerprints can relink identity; replace them
                # with opaque one-way tombstones while preserving row uniqueness/audit shape.
                refs = self.db.execute(
                    "SELECT reference_id FROM crm_external_references WHERE customer_id=? ORDER BY reference_id",
                    (customer_id,),
                ).fetchall()
                for index, ref in enumerate(refs):
                    self.db.execute(
                        "UPDATE crm_external_references SET external_id=?,raw_identity_fingerprint=NULL WHERE reference_id=?",
                        (f"{tombstone_prefix}:{index}", ref["reference_id"]),
                    )

                # Child tables that can contain direct/free-text PII.
                self.db.execute("DELETE FROM crm_addresses WHERE customer_id=?", (customer_id,))
                self.db.execute("DELETE FROM crm_tags WHERE customer_id=?", (customer_id,))
                self.db.execute(
                    "UPDATE crm_consents SET evidence=NULL WHERE customer_id=?", (customer_id,)
                )
                self.db.execute(
                    "UPDATE crm_activities SET metadata_json='{}' WHERE customer_id=?", (customer_id,)
                )
                self.db.execute(
                    """UPDATE crm_interactions SET content='',author='privacy-redacted',due_at=NULL,
                    deleted_at=COALESCE(deleted_at,?),updated_at=? WHERE customer_id=?""",
                    (stamp, stamp, customer_id),
                )
                self.db.execute(
                    """UPDATE crm_merge_history SET snapshot_json='{"privacy":"REDACTED"}'
                    WHERE primary_customer_id=? OR secondary_customer_id=?""",
                    (customer_id, customer_id),
                )

                evidence = {
                    "direct_customer_pii": "REMOVED",
                    "external_identity_links": "TOMBSTONED",
                    "identity_fingerprints": "REMOVED",
                    "addresses": "REMOVED",
                    "tags": "REMOVED",
                    "consent_free_text_evidence": "REMOVED",
                    "activity_metadata": "REMOVED",
                    "interaction_content": "REMOVED",
                    "merge_snapshots": "REDACTED",
                    "sales_links_preserved": True,
                    "loyalty_ledger_preserved": True,
                    "external_provider_write": False,
                }
                self.db.execute(
                    "INSERT INTO crm_privacy_events VALUES(?,?,?,?,?,?,?,?)",
                    (event_id, customer_id, "ANONYMISE", "COMPLETED", actor, reason, stamp, json.dumps(evidence, sort_keys=True)),
                )
        except sqlite3.DatabaseError as exc:
            raise CRMPrivacyError("CRM anonymisation transaction failed; no partial result is accepted") from exc

        result = self.status(customer_id)
        if any((
            result["direct_identifier_fields_present"], result["identity_fingerprints_present"],
            result["addresses_present"], result["nonempty_interactions_present"],
        )):
            raise CRMPrivacyError("CRM anonymisation post-condition failed")
        return {**result, "result": "ANONYMISED"}

    def events(self, customer_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT event_id,customer_id,action,result,actor,reason,occurred_at,evidence_json "
            "FROM crm_privacy_events WHERE customer_id=? ORDER BY occurred_at",
            (customer_id,),
        ).fetchall()
        return [
            {**{k: row[k] for k in row.keys() if k != "evidence_json"}, "evidence": json.loads(row["evidence_json"])}
            for row in rows
        ]
