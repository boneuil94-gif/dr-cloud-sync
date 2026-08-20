"""Local salted suppression keys for erased CRM provider identities.

The registry deliberately stores no provider external identifier and no customer id.
It exists only to prevent a later provider sync from recreating PII that was erased.
"""
from __future__ import annotations

import hashlib
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_privacy_config(
 config_id INTEGER PRIMARY KEY CHECK(config_id=1),
 suppression_salt TEXT NOT NULL
);
INSERT OR IGNORE INTO crm_privacy_config(config_id,suppression_salt)
VALUES(1, lower(hex(randomblob(32))));
CREATE TABLE IF NOT EXISTS crm_identity_suppressions(
 provider TEXT NOT NULL,
 external_id_digest TEXT NOT NULL,
 suppressed_at TEXT NOT NULL,
 PRIMARY KEY(provider,external_id_digest)
);
"""


def ensure_suppression_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


def suppression_digest(db: sqlite3.Connection, provider: str, external_id: str) -> str:
    """Return a database-local salted digest; the raw external id is never persisted."""
    row = db.execute(
        "SELECT suppression_salt FROM crm_privacy_config WHERE config_id=1"
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError("CRM suppression salt is unavailable")
    material = f"{row[0]}\0{provider.strip().upper()}\0{str(external_id).strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def is_suppressed(db: sqlite3.Connection, provider: str, external_id: str) -> bool:
    digest = suppression_digest(db, provider, external_id)
    return db.execute(
        "SELECT 1 FROM crm_identity_suppressions WHERE provider=? AND external_id_digest=?",
        (provider.strip().upper(), digest),
    ).fetchone() is not None


def record_suppression(
    db: sqlite3.Connection, provider: str, external_id: str, suppressed_at: str
) -> None:
    digest = suppression_digest(db, provider, external_id)
    db.execute(
        "INSERT OR IGNORE INTO crm_identity_suppressions(provider,external_id_digest,suppressed_at) VALUES(?,?,?)",
        (provider.strip().upper(), digest, suppressed_at),
    )
