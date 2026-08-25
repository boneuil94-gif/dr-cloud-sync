import sqlite3
from pathlib import Path

from dr_cloud_sync.recovery_watermark import capture_recovery_watermark


COLUMNS = """source_id text, source_type text, provider text, status text, enabled integer,
last_success_at text, stale_after_seconds integer, data_min_at text, data_max_at text,
records_available integer, cursor text, last_error text"""


def _database(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(f"create table data_sources ({COLUMNS})")
        db.execute(
            "insert into data_sources values (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("marketing_intelligence", "marketing", "local", "CONNECTED", 1,
             "2026-01-01T00:00:01Z", 3600, None, None, None, None, None),
        )
        db.execute("create table marketing_audit(event_type text,entity_type text,entity_id text,occurred_at text)")
        db.execute("create table marketing_proposals(proposal_id text,opportunity_id text,created_at text)")
        db.execute("create table marketing_opportunities(opportunity_id text,detected_at text)")
        db.execute("create table marketing_proposal_products(proposal_id text)")
        db.execute("create table marketing_hypotheses(hypothesis_id text,measured_at text)")
    return path


def test_marketing_intelligence_orders_mixed_fractional_timestamps_chronologically(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("insert into marketing_opportunities values('intel-o','2026-01-01T00:00:00Z')")
        db.execute("insert into marketing_proposals values('intel-p','intel-o','2026-01-01T00:00:00Z')")
        db.execute("insert into marketing_proposal_products values('intel-p')")
        db.execute("insert into marketing_audit values('INTELLIGENCE_PROPOSAL_GENERATED','proposal','intel-p','2026-01-01T00:00:00Z')")
        db.execute("insert into marketing_hypotheses values('hypothesis:later','2026-01-01T00:00:00.500000Z')")

    result = capture_recovery_watermark(path)
    source = next(item for item in result["sources"] if item["source_id"] == "marketing_intelligence")

    assert source["classification"] == "ELIGIBLE"
    assert source["data_min_at"] == "2026-01-01T00:00:00Z"
    assert source["data_max_at"] == "2026-01-01T00:00:00.500000Z"
