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

from .crm_suppression import ensure_suppression_schema, record_suppression


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
            ensure_suppression_schema(self.db)

    def retention_policy(self) -> dict:
        return {
            "status": "NOT_CONFIGURED",
            "automatic_deletion": False,
            "retention_days": None,
            "reason": "No authoritative CRM PII retention duration is configured.",
        }

    def _postcondition_counts(self, customer_id: str) -> dict[str, int]:
        row = self.db.execute(
            "SELECT display_name,first_name,last_name,email_normalized,phone_normalized,"
            "birth_date,language,country,city,postal_code FROM crm_customers WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        if not row:
            raise KeyError(customer_id)
        direct = sum(value is not None and str(value).strip() != "" for value in row)
        return {
            "direct_identifiers": direct,
            "identity_fingerprints": self.db.execute(
                "SELECT count(*) FROM crm_external_references WHERE customer_id=? AND raw_identity_fingerprint IS NOT NULL",
                (customer_id,),
            ).fetchone()[0],
            "addresses": self.db.execute(
                "SELECT count(*) FROM crm_addresses WHERE customer_id=?", (customer_id,)
            ).fetchone()[0],
            "nonempty_interactions": self.db.execute(
                "SELECT count(*) FROM crm_interactions WHERE customer_id=? AND length(trim(content))>0",
                (customer_id,),
            ).fetchone()[0],
            "identity_links": self.db.execute(
                "SELECT count(*) FROM crm_identities WHERE customer_id=? OR candidate_customer_id=?",
                (customer_id, customer_id),
            ).fetchone()[0],
        }

    def status(self, customer_id: str) -> dict:
        row = self.db.execute(
            "SELECT customer_id,status,anonymised_at,deleted_at FROM crm_customers WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        if not row:
            raise KeyError(customer_id)
        counts = self._postcondition_counts(customer_id)
        last = self.db.execute(
            "SELECT action,result,occurred_at FROM crm_privacy_events WHERE customer_id=? ORDER BY occurred_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
        return {
            "customer_id": customer_id,
            "status": row["status"],
            "anonymised_at": row["anonymised_at"],
            "deleted_at": row["deleted_at"],
            "direct_identifier_fields_present": counts["direct_identifiers"],
            "identity_fingerprints_present": counts["identity_fingerprints"],
            "addresses_present": counts["addresses"],
            "nonempty_interactions_present": counts["nonempty_interactions"],
            "identity_links_present": counts["identity_links"],
            "last_privacy_action": dict(last) if last else None,
            "external_provider_write": False,
            "retention_policy": self.retention_policy(),
        }

    def anonymise(self, customer_id: str, *, actor: str, reason: str) -> dict:
        actor = str(actor or "").strip()
        reason = str(reason or "").strip()
        if not actor or not reason:
            raise ValueError("actor and reason are required")
        if not self.db.execute(
            "SELECT 1 FROM crm_customers WHERE customer_id=?", (customer_id,)
        ).fetchone():
            raise KeyError(customer_id)

        stamp = _now()
        event_id = f"privacy:{uuid4()}"
        tombstone_prefix = f"anonymised:{uuid4()}"
        already = False
        try:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.db.execute(
                "SELECT anonymised_at FROM crm_customers WHERE customer_id=?", (customer_id,)
            ).fetchone()
            if not current:
                raise KeyError(customer_id)
            if current["anonymised_at"]:
                already = True
            else:
                refs = self.db.execute(
                    "SELECT reference_id,provider,external_id FROM crm_external_references "
                    "WHERE customer_id=? ORDER BY reference_id",
                    (customer_id,),
                ).fetchall()
                for ref in refs:
                    record_suppression(self.db, ref["provider"], ref["external_id"], stamp)

                updated = self.db.execute(
                    """UPDATE crm_customers SET
                    display_name=NULL,first_name=NULL,last_name=NULL,email_normalized=NULL,
                    phone_normalized=NULL,birth_date=NULL,language=NULL,country=NULL,city=NULL,
                    postal_code=NULL,status='ANONYMISED',data_quality='ANONYMOUS',
                    quality_details_json='{}',anonymised_at=?,updated_at=?
                    WHERE customer_id=? AND anonymised_at IS NULL""",
                    (stamp, stamp, customer_id),
                )
                if updated.rowcount != 1:
                    already = True
                else:
                    for index, ref in enumerate(refs):
                        self.db.execute(
                            "UPDATE crm_external_references SET external_id=?,raw_identity_fingerprint=NULL WHERE reference_id=?",
                            (f"{tombstone_prefix}:{index}", ref["reference_id"]),
                        )

                    self.db.execute(
                        "DELETE FROM crm_identities WHERE customer_id=? OR candidate_customer_id=?",
                        (customer_id, customer_id),
                    )
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

                    counts = self._postcondition_counts(customer_id)
                    if any(counts.values()):
                        raise CRMPrivacyError("CRM anonymisation post-condition failed")

                    evidence = {
                        "direct_customer_pii": "REMOVED",
                        "external_identity_links": "SALTED_SUPPRESSION_AND_TOMBSTONE",
                        "identity_fingerprints": "REMOVED",
                        "duplicate_identity_links": "REMOVED",
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
            self.db.commit()
        except Exception as exc:
            if self.db.in_transaction:
                self.db.rollback()
            if isinstance(exc, (KeyError, ValueError, CRMPrivacyError)):
                raise
            if isinstance(exc, sqlite3.DatabaseError):
                raise CRMPrivacyError("CRM anonymisation transaction failed; no partial result is accepted") from exc
            raise

        if already:
            return {**self.status(customer_id), "result": "ALREADY_ANONYMISED"}
        return {**self.status(customer_id), "result": "ANONYMISED"}

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
