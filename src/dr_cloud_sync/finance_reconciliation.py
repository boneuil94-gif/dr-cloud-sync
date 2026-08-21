"""Read-only, fail-closed reconciliation between SumUp payouts and bank credits.

A payout is only MATCHED when one and only one booked bank credit has the same
currency, exact decimal amount and exact normalized provider reference. Missing,
malformed or contended matches remain unresolved/ambiguous; no fuzzy matching is used.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import sqlite3
from pathlib import Path


def _norm_reference(value: object) -> str | None:
    text = " ".join(str(value or "").strip().upper().split())
    return text or None


def _money(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_bound(rows, field: str, selector) -> str | None:
    values = [parsed for row in rows if (parsed := _timestamp(row[field])) is not None]
    return selector(values).isoformat() if values else None


def _unmeasurable(reason: str) -> dict:
    return {
        "status": "UNMEASURABLE",
        "reason": reason,
        "payouts_total": None,
        "matched": None,
        "unresolved": None,
        "ambiguous": None,
        "coverage_ratio": None,
        "source_evidence": None,
    }


def _coverage_diagnosis(*, payout_total: int, payout_with_reference: int, bank_total: int, bank_with_reference: int) -> str:
    """Classify only what the local ledgers prove; never infer provider exhaustiveness."""
    if payout_total == 0:
        return "NO_LOCAL_SUMUP_PAYOUTS"
    if bank_total == 0:
        return "NO_LOCAL_QONTO_BOOKED_CREDITS"
    if payout_with_reference == 0:
        return "LOCAL_SUMUP_PAYOUT_REFERENCES_MISSING"
    if bank_with_reference == 0:
        return "LOCAL_QONTO_BOOKED_CREDIT_REFERENCES_MISSING"
    if payout_with_reference < payout_total:
        return "LOCAL_SUMUP_PAYOUT_REFERENCE_COVERAGE_PARTIAL"
    if bank_with_reference < bank_total:
        return "LOCAL_QONTO_BOOKED_CREDIT_REFERENCE_COVERAGE_PARTIAL"
    return "LOCAL_EXACT_MATCH_GAP_REMAINS"


def _source_evidence(payouts, credits, *, bank_provider: str) -> dict:
    payout_with_reference = sum(1 for row in payouts if _norm_reference(row["reference"]))
    bank_with_reference = sum(1 for row in credits if _norm_reference(row["reference"]))
    bank_total = len(credits)
    payout_total = len(payouts)
    return {
        "coverage_diagnosis": _coverage_diagnosis(
            payout_total=payout_total,
            payout_with_reference=payout_with_reference,
            bank_total=bank_total,
            bank_with_reference=bank_with_reference,
        ),
        "diagnosis_scope": "LOCAL_LEDGER_ONLY",
        "provider_exhaustiveness_inferred": False,
        "payouts": {
            "total": payout_total,
            "with_reference": payout_with_reference,
            "without_reference": payout_total - payout_with_reference,
            "reference_coverage_ratio": (payout_with_reference / payout_total) if payout_total else None,
            "latest_imported_at": _timestamp_bound(payouts, "imported_at", max),
        },
        "bank_credits": {
            "provider": bank_provider,
            "booked_credits_total": bank_total,
            "with_reference": bank_with_reference,
            "without_reference": bank_total - bank_with_reference,
            "reference_coverage_ratio": (bank_with_reference / bank_total) if bank_total else None,
            "booked_at_min": _timestamp_bound(credits, "booked_at", min),
            "booked_at_max": _timestamp_bound(credits, "booked_at", max),
            "latest_imported_at": _timestamp_bound(credits, "imported_at", max),
            "presence": "BOOKED_CREDITS_PRESENT" if bank_total else "NO_BOOKED_CREDITS",
        },
    }


def reconcile_sumup_payouts_to_bank(path: Path | str, *, bank_provider: str = "qonto") -> dict:
    """Return sanitized reconciliation facts without mutating or creating a ledger."""
    ledger_path = Path(path)
    if not ledger_path.is_file():
        return _unmeasurable("REQUIRED_LEDGER_MISSING")

    # mode=ro enforces the function's read-only contract at SQLite level.
    db = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"sumup_payouts", "bank_transactions"}
        if not required <= tables:
            return _unmeasurable("REQUIRED_LEDGER_MISSING")

        payouts = list(db.execute(
            "SELECT payout_id, amount, currency, reference, status, imported_at FROM sumup_payouts ORDER BY payout_id"
        ))
        credits = list(db.execute(
            "SELECT transaction_id, amount, currency, reference, status, booked_at, imported_at FROM bank_transactions "
            "WHERE provider=? AND direction='CREDIT' AND status='BOOKED' ORDER BY transaction_id",
            (bank_provider,),
        ))
        source_evidence = _source_evidence(payouts, credits, bank_provider=bank_provider)

        provisional = []
        single_candidate_ids = []
        for payout in payouts:
            reference = _norm_reference(payout["reference"])
            amount = _money(payout["amount"])
            currency = str(payout["currency"] or "").upper()
            if not reference:
                provisional.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "PAYOUT_REFERENCE_MISSING"})
                continue
            if amount is None or not currency:
                provisional.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "PAYOUT_AMOUNT_OR_CURRENCY_INVALID"})
                continue

            candidates = []
            for bank in credits:
                bank_amount = _money(bank["amount"])
                if bank_amount is None or bank_amount != amount:
                    continue
                if str(bank["currency"] or "").upper() != currency:
                    continue
                if _norm_reference(bank["reference"]) != reference:
                    continue
                candidates.append(bank["transaction_id"])

            if len(candidates) == 1:
                candidate = candidates[0]
                single_candidate_ids.append(candidate)
                provisional.append({"payout_id": payout["payout_id"], "status": "CANDIDATE", "bank_transaction_id": candidate})
            elif len(candidates) > 1:
                provisional.append({"payout_id": payout["payout_id"], "status": "AMBIGUOUS", "reason": "MULTIPLE_EXACT_BANK_MATCHES"})
            else:
                provisional.append({"payout_id": payout["payout_id"], "status": "UNRESOLVED", "reason": "NO_EXACT_BANK_MATCH"})

        contention = Counter(single_candidate_ids)
        matched = unresolved = ambiguous = 0
        rows = []
        for row in provisional:
            if row["status"] == "CANDIDATE":
                candidate = row["bank_transaction_id"]
                if contention[candidate] == 1:
                    row = {"payout_id": row["payout_id"], "status": "MATCHED", "bank_transaction_id": candidate}
                    matched += 1
                else:
                    row = {"payout_id": row["payout_id"], "status": "AMBIGUOUS", "reason": "BANK_CREDIT_CONTENDED"}
                    ambiguous += 1
            elif row["status"] == "AMBIGUOUS":
                ambiguous += 1
            else:
                unresolved += 1
            rows.append(row)

        total = len(payouts)
        return {
            "status": "NO_DATA" if total == 0 else "MEASURABLE",
            "payouts_total": total,
            "matched": matched,
            "unresolved": unresolved,
            "ambiguous": ambiguous,
            "coverage_ratio": (matched / total) if total else None,
            "source_evidence": source_evidence,
            "rows": rows,
        }
    finally:
        db.close()
