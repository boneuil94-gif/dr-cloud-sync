"""Sanitized aggregate evidence for local Qonto bank transaction shape.

Reads only the local SQLite ledger in mode=ro. No row identifiers, references,
free-form labels, provider calls, credentials or mutations are emitted.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

SCOPE = "LOCAL_LEDGER_ONLY"
_ALLOWED_STATUSES = {"BOOKED", "COMPLETED", "PENDING", "REVERSED", "DECLINED", "CANCELLED", "FAILED"}


def _status_bucket(value):
    status = str(value or "").strip().upper()
    return status if status in _ALLOWED_STATUSES else "OTHER"


def qonto_local_transaction_shape(path: Path | str) -> dict:
    ledger = Path(path)
    if not ledger.is_file():
        return {"status":"UNMEASURABLE","reason":"LOCAL_DATABASE_MISSING","provider_exhaustiveness_inferred":False}
    db = sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "bank_transactions" not in tables:
            return {"status":"UNMEASURABLE","reason":"BANK_LEDGER_MISSING","provider_exhaustiveness_inferred":False}
        rows = list(db.execute(
            "SELECT direction,status,reference FROM bank_transactions WHERE lower(provider)='qonto'"
        ))
        status_counts = {}
        direction_counts = {"CREDIT":0,"DEBIT":0,"OTHER":0}
        credits_with_reference = 0
        for row in rows:
            bucket = _status_bucket(row["status"])
            status_counts[bucket] = status_counts.get(bucket, 0) + 1
            direction = str(row["direction"] or "").upper()
            direction = direction if direction in {"CREDIT","DEBIT"} else "OTHER"
            direction_counts[direction] += 1
            if direction == "CREDIT" and str(row["reference"] or "").strip():
                credits_with_reference += 1
        total = len(rows)
        credits = direction_counts["CREDIT"]
        booked_credits = db.execute(
            "SELECT count(*) FROM bank_transactions WHERE lower(provider)='qonto' AND direction='CREDIT' AND status='BOOKED'"
        ).fetchone()[0]
        completed_credits = db.execute(
            "SELECT count(*) FROM bank_transactions WHERE lower(provider)='qonto' AND direction='CREDIT' AND status='COMPLETED'"
        ).fetchone()[0]
        if total == 0:
            cause = "NO_LOCAL_QONTO_TRANSACTIONS"
        elif credits == 0:
            cause = "NO_LOCAL_QONTO_CREDITS"
        elif booked_credits == 0 and completed_credits > 0:
            cause = "QONTO_LOCAL_CREDITS_USE_COMPLETED_STATUS"
        elif booked_credits == 0:
            cause = "QONTO_LOCAL_CREDITS_PRESENT_WITHOUT_BOOKED_STATUS"
        else:
            cause = "QONTO_LOCAL_BOOKED_CREDITS_PRESENT"
        return {
            "status":"MEASURABLE",
            "evidence_scope":SCOPE,
            "provider":"Qonto",
            "provider_exhaustiveness_inferred":False,
            "cause":cause,
            "transactions_total":total,
            "direction_counts":direction_counts,
            "status_counts":dict(sorted(status_counts.items())),
            "credits":{"total":credits,"booked":booked_credits,"completed":completed_credits,
                       "with_reference":credits_with_reference,
                       "reference_coverage_ratio":credits_with_reference/credits if credits else None},
            "safety":{"database_read_only":True,"provider_network_calls":False,"mutations":False,
                      "row_level_identifiers_emitted":False,"reference_values_emitted":False},
        }
    finally:
        db.close()
