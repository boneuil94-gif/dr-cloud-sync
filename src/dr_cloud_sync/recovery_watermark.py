"""Sanitised, source-aware recovery watermarks and comparisons.

``data_sources`` defines which business sources exist and whether they are enabled.
For a small, explicit allow-list of durable local ledgers, recovery measurement
uses committed business timestamps/counts from the SQLite snapshot itself.  Some
source rows are explicit derived projections of a parent source; they are kept in
evidence but never counted as independent RPO obligations.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
SAFE_COLUMNS = ("source_id", "source_type", "provider", "status", "enabled",
                "last_success_at", "stale_after_seconds", "data_min_at",
                "data_max_at", "records_available")

# Explicit source -> durable local business projection. Table and column names
# are constants, never caller-controlled SQL. A source not listed here falls
# back to its data_sources control-plane watermark.
DURABLE_LEDGER_PROJECTIONS = {
    "bank": ("bank_transactions", "booked_at", None),
    "shopcaisse_sales": ("sales", "sold_at", ("source", "SHOPCAISSE")),
    "prestashop_sales": ("sales", "sold_at", ("source", "PRESTASHOP")),
    "sumup_transactions": ("sumup_transactions", "timestamp", None),
    # Purchases are handled explicitly below because both order headers and
    # mutable order lines are durable business truth.
    "purchases": ("purchase_orders", "updated_at", None),
    # Stock RPO only follows movements that are actually applied. ``applied_at``
    # is the durable business-progress timestamp; legacy rows without it remain
    # explicitly unmeasurable rather than falling back to an earlier timestamp.
    "stock": ("stock_movements", "applied_at", ("status", "APPLIED")),
}

# These are not independent provider feeds. They are materialised while the
# transaction-detail sync imports ``sumup_transactions``. Counting them as
# separate RPO sources would overstate missing coverage and imply fake jobs.
DERIVED_SOURCE_PARENTS = {
    "sumup_fees": "sumup_transactions",
    "sumup_refunds": "sumup_transactions",
    "sumup_chargebacks": "sumup_transactions",
}


def _iso(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _classification(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    if not row.get("enabled"):
        return "DISABLED"
    if status in {"NOT_CONFIGURED", "UNCONFIGURED"}:
        return "NOT_CONFIGURED"
    if row.get("derived_from"):
        return "DERIVED"
    records = row.get("records_available")
    if records is None:
        return "UNMEASURABLE"
    if records <= 0:
        return "NO_DATA"
    if row.get("data_max_at") and not _iso(row["data_max_at"]):
        return "INVALID_TIMESTAMP"
    if not row.get("data_max_at"):
        return "UNMEASURABLE"
    return "ELIGIBLE"


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return set()
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _purchase_ledger_projection(db: sqlite3.Connection) -> dict | None:
    """Project purchase progress across order headers and mutable child lines.

    Both tables carry durable ``updated_at`` timestamps. Counting both tables
    also makes line deletion visible to comparison logic; a count regression is
    deliberately treated as unmeasurable instead of a zero-second RPO.
    """
    required = {
        "purchase_orders": {"updated_at"},
        "purchase_order_lines": {"updated_at"},
    }
    if any(not columns <= _table_columns(db, table) for table, columns in required.items()):
        return None
    try:
        row = db.execute(
            "SELECT COUNT(*),COUNT(updated_at),MIN(updated_at),MAX(updated_at) "
            "FROM ("
            "SELECT updated_at FROM purchase_orders "
            "UNION ALL "
            "SELECT updated_at FROM purchase_order_lines"
            ")"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    count = int(row[0] or 0)
    timestamped = int(row[1] or 0)
    measurable = count == timestamped
    return {
        "records_available": count,
        "data_min_at": row[2] if count and measurable else None,
        "data_max_at": row[3] if count and measurable else None,
        "watermark_origin": "DURABLE_LEDGER",
        "watermark_table": "purchase_orders+purchase_order_lines",
        "watermark_timestamp_column": "updated_at",
    }


def _durable_ledger_projection(db: sqlite3.Connection, source_id: str) -> dict | None:
    """Return count/min/max from one approved committed business ledger.

    ``None`` means the projection cannot be measured (missing table/columns), so
    the caller keeps the existing control-plane facts. A real empty ledger is
    returned as count=0 and deliberately clears stale timestamps. Any matching
    row lacking its durable timestamp makes the whole projection fail closed.
    """
    if source_id == "purchases":
        return _purchase_ledger_projection(db)
    definition = DURABLE_LEDGER_PROJECTIONS.get(source_id)
    if not definition:
        return None
    table, timestamp_column, filter_definition = definition
    columns = _table_columns(db, table)
    required = {timestamp_column}
    if filter_definition:
        required.add(filter_definition[0])
    if not required <= columns:
        return None

    where_sql = ""
    params: tuple[object, ...] = ()
    if filter_definition:
        filter_column, filter_value = filter_definition
        where_sql = f" WHERE {filter_column}=?"
        params = (filter_value,)
    try:
        row = db.execute(
            f"SELECT COUNT(*),COUNT({timestamp_column}),MIN({timestamp_column}),MAX({timestamp_column}) "
            f"FROM {table}{where_sql}",
            params,
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None

    count = int(row[0] or 0)
    timestamped = int(row[1] or 0)
    measurable = count == timestamped
    return {
        "records_available": count,
        "data_min_at": row[2] if count and measurable else None,
        "data_max_at": row[3] if count and measurable else None,
        "watermark_origin": "DURABLE_LEDGER",
        "watermark_table": table,
        "watermark_timestamp_column": timestamp_column,
    }


def capture_recovery_watermark(database: Path, *, captured_at=None,
                               captured_from: str = "LIVE_DATABASE") -> dict:
    """Read SQLite read-only and return only approved source/business facts."""
    when = _iso(captured_at) if captured_at is not None else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if when is None:
        raise ValueError("captured_at must be an ISO-8601 timestamp with timezone")
    rows = []
    projections: dict[str, dict] = {}
    table_available = False
    try:
        with sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True) as db:
            table_available = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_sources'"
            ).fetchone() is not None
            if table_available:
                columns = {r[1] for r in db.execute("PRAGMA table_info(data_sources)")}
                if set(SAFE_COLUMNS) <= columns:
                    db.row_factory = sqlite3.Row
                    rows = [dict(r) for r in db.execute(
                        f"SELECT {','.join(SAFE_COLUMNS)} FROM data_sources ORDER BY source_id")]
                    for row in rows:
                        projection = _durable_ledger_projection(db, str(row["source_id"]))
                        if projection is not None:
                            projections[str(row["source_id"])] = projection
    except sqlite3.Error:
        table_available = False
        rows = []
        projections = {}

    sources = []
    for row in rows:
        clean = {key: row.get(key) for key in SAFE_COLUMNS}
        clean["enabled"] = bool(clean["enabled"])
        source_id = str(clean["source_id"])
        derived_from = DERIVED_SOURCE_PARENTS.get(source_id)
        if derived_from:
            clean["derived_from"] = derived_from
            clean["watermark_origin"] = "DERIVED_FROM_PARENT"
        else:
            projection = projections.get(source_id)
            if projection is not None:
                clean.update(projection)
            else:
                clean["watermark_origin"] = "DATA_SOURCE_CONTROL_PLANE"
        clean["classification"] = _classification(clean)
        # Canonicalise valid timestamps; preserve invalid values solely so the
        # explicit INVALID_TIMESTAMP classification remains auditable.
        for key in ("last_success_at", "data_min_at", "data_max_at"):
            if clean[key] is not None and _iso(clean[key]):
                clean[key] = _iso(clean[key])
        sources.append(clean)
    ignored = {"DISABLED", "NOT_CONFIGURED", "NO_DATA", "DERIVED"}
    eligible = [s for s in sources if s["classification"] not in ignored]
    measured = [s for s in eligible if s["classification"] == "ELIGIBLE"]
    missing = len(eligible) - len(measured)
    aggregate = max((s["data_max_at"] for s in measured), default=None)
    confidence = "MEDIUM" if eligible and not missing else "LOW" if measured else "UNKNOWN"
    return {"schema_version": SCHEMA_VERSION, "captured_from": captured_from,
            "captured_at": when, "aggregate_data_max_at": aggregate,
            "confidence": confidence, "table_available": table_available,
            "coverage": {"eligible_sources": len(eligible),
                         "measured_sources": len(measured),
                         "missing_data_max_sources": missing,
                         "durable_ledger_sources": len(projections),
                         "derived_sources": sum(s["classification"] == "DERIVED" for s in sources)},
            "sources": sources}


def validate_recovery_watermark(value: object) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return False
    if not _iso(value.get("captured_at")):
        return False
    aggregate = value.get("aggregate_data_max_at")
    if aggregate is not None and not _iso(aggregate):
        return False
    return isinstance(value.get("coverage"), dict) and isinstance(value.get("sources"), list)


def compare_recovery_watermarks(live: dict, backup: dict) -> dict:
    """Compare independent business progress; max per-source lag is observed RPO.

    Count growth without comparable timestamps is reported, never converted to
    a zero-second RPO. Count regression is also fail-closed because it can mean
    a durable deletion that has no standalone timestamp. NOT_CONFIGURED,
    DISABLED, NO_DATA and DERIVED sources are neutral because a derived
    projection inherits its parent source's RPO.
    """
    base = {"comparable_sources": 0, "unmeasurable_sources": 0,
            "business_data_gap_seconds": None, "sync_progress_gap_seconds": None,
            "record_count_gap": 0, "observed_rpo_seconds": None,
            "confidence": "UNKNOWN"}
    if not (validate_recovery_watermark(live) and validate_recovery_watermark(backup)):
        return base
    ignored = {"DISABLED", "NOT_CONFIGURED", "NO_DATA", "DERIVED"}
    live_sources = {s.get("source_id"): s for s in live["sources"] if s.get("classification") not in ignored}
    old_sources = {s.get("source_id"): s for s in backup["sources"] if s.get("classification") not in ignored}
    gaps, sync_gaps = [], []
    for source_id, current in live_sources.items():
        old = old_sources.get(source_id)
        current_count, old_count = current.get("records_available"), old.get("records_available") if old else None
        if isinstance(current_count, int) and isinstance(old_count, int):
            base["record_count_gap"] += max(0, current_count - old_count)
            if current_count < old_count:
                base["unmeasurable_sources"] += 1
                continue
        current_at = _iso(current.get("data_max_at")); old_at = _iso(old.get("data_max_at")) if old else None
        if current_at and old_at:
            delta = max(0, (datetime.fromisoformat(current_at.replace("Z", "+00:00")) -
                            datetime.fromisoformat(old_at.replace("Z", "+00:00"))).total_seconds())
            gaps.append(delta); base["comparable_sources"] += 1
            live_sync = _iso(current.get("last_success_at")); old_sync = _iso(old.get("last_success_at"))
            if live_sync and old_sync:
                sync_gaps.append(max(0, (datetime.fromisoformat(live_sync.replace("Z", "+00:00")) - datetime.fromisoformat(old_sync.replace("Z", "+00:00"))).total_seconds()))
        else:
            base["unmeasurable_sources"] += 1
    if gaps:
        base["business_data_gap_seconds"] = base["observed_rpo_seconds"] = max(gaps)
        base["sync_progress_gap_seconds"] = max(sync_gaps) if sync_gaps else None
        total = len(live_sources)
        base["confidence"] = "HIGH" if base["comparable_sources"] == total and not base["unmeasurable_sources"] else "MEDIUM"
    return base
