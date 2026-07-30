"""Durable local administrator credential management.

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes.  PBKDF2 is provided by
Python's standard library, avoiding a new native dependency while retaining a
purpose-built, deliberately expensive password hashing primitive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import uuid


ADMIN_ACCOUNT_ID = "local-admin"
PBKDF2_ITERATIONS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Credential:
    account_id: str
    password_hash: str
    password_changed_at: str
    session_version: int


class CredentialStore:
    """Single-account credential adapter with additive, idempotent schema setup."""

    def __init__(self, database: Path, bootstrap_password: str):
        self.database = database
        self.db = sqlite3.connect(database, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS local_credentials(
          account_id TEXT PRIMARY KEY,
          password_hash TEXT NOT NULL,
          password_changed_at TEXT NOT NULL,
          session_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS activity_logs(
          id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, data TEXT NOT NULL
        );
        """)
        if not self.get():
            if not bootstrap_password:
                raise ValueError("Un mot de passe administrateur initial est requis")
            # Migration: only the derived hash enters SQLite; never the env secret.
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO local_credentials VALUES(?,?,?,1)",
                    (ADMIN_ACCOUNT_ID, hash_password(bootstrap_password), _now()),
                )

    def get(self) -> Credential | None:
        row = self.db.execute(
            "SELECT account_id,password_hash,password_changed_at,session_version "
            "FROM local_credentials WHERE account_id=?", (ADMIN_ACCOUNT_ID,)
        ).fetchone()
        return Credential(**dict(row)) if row else None

    def verify(self, password: str) -> bool:
        credential = self.get()
        return bool(credential and verify_password(password, credential.password_hash))

    def change_password(self, current_password: str, new_password: str, actor: str) -> int:
        """Atomically replace the hash, bump sessions and write secret-free audit."""
        new_hash = hash_password(new_password)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT password_hash,session_version FROM local_credentials WHERE account_id=?",
                (ADMIN_ACCOUNT_ID,),
            ).fetchone()
            if not row or not verify_password(current_password, row["password_hash"]):
                raise PermissionError("Mot de passe actuel incorrect")
            changed_at = _now()
            version = int(row["session_version"]) + 1
            self.db.execute(
                "UPDATE local_credentials SET password_hash=?,password_changed_at=?,session_version=? "
                "WHERE account_id=?",
                (new_hash, changed_at, version, ADMIN_ACCOUNT_ID),
            )
            activity_id = str(uuid.uuid4())
            activity = {
                "event_type": "PASSWORD_CHANGED", "drcloud_product_key": ADMIN_ACCOUNT_ID,
                "source": "SECURITY", "metadata": {"actor": actor, "success": True},
                "id": activity_id, "timestamp": changed_at,
            }
            self.db.execute(
                "INSERT INTO activity_logs VALUES(?,?,?)",
                (activity_id, changed_at, json.dumps(activity)),
            )
            self.db.commit()
            return version
        except Exception:
            self.db.rollback()
            raise
