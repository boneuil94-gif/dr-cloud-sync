import json
import sqlite3
from pathlib import Path

from dr_cloud_sync.recovery_watermark import capture_recovery_watermark, compare_recovery_watermarks

COLUMNS = """source_id text, source_type text, provider text, status text, enabled integer,
last_success_at text, stale_after_seconds integer, data_min_at text, data_max_at text,
records_available integer, cursor text, last_error text"""


def database(tmp_path: Path, rows=()):
    path=tmp_path/"db.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(f"create table data_sources ({COLUMNS})")
        db.executemany("insert into data_sources values (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return path


def row(source="sales", status="CONNECTED", enabled=1, maximum="2026-08-19T10:00:00Z", records=10):
    return (source,"sales","provider",status,enabled,"2026-08-19T10:01:00Z",3600,
            "2026-08-01T00:00:00Z",maximum,records,"sensitive-cursor","sensitive-error")


def test_absent_table_is_unknown_and_sanitized(tmp_path):
    path=tmp_path/"empty.sqlite"; sqlite3.connect(path).close()
    result=capture_recovery_watermark(path)
    assert result["confidence"] == "UNKNOWN" and not result["table_available"]
    assert "cursor" not in str(result) and "last_error" not in str(result)


def test_classification_and_deterministic_aggregate(tmp_path):
    result=capture_recovery_watermark(database(tmp_path,[
        row("z", maximum="2026-08-19T12:00:00+00:00"), row("a"),
        row("off", enabled=0), row("none", status="NOT_CONFIGURED"),
        row("invalid", maximum="yesterday"), row("empty", records=0, maximum=None)]))
    classes={x["source_id"]:x["classification"] for x in result["sources"]}
    assert classes == {"a":"ELIGIBLE","empty":"NO_DATA","invalid":"INVALID_TIMESTAMP","none":"NOT_CONFIGURED","off":"DISABLED","z":"ELIGIBLE"}
    assert result["aggregate_data_max_at"] == "2026-08-19T12:00:00Z"
    assert result["confidence"] != "HIGH"


def test_sumup_payout_projection_requires_real_provider_business_timestamp(tmp_path):
    path=database(tmp_path,[row("sumup_payouts", maximum=None, records=None)])
    with sqlite3.connect(path) as db:
        db.execute("create table sumup_payouts(payout_date text not null,raw_json text not null)")
        db.executemany("insert into sumup_payouts values(?,?)",[
            ("2026-08-20", json.dumps({"payout_date":"2026-08-20"})),
            ("2026-08-21T09:30:00+00:00", json.dumps({"date":"2026-08-21T09:30:00Z"})),
        ])
    result=capture_recovery_watermark(path)
    payout=next(x for x in result["sources"] if x["source_id"]=="sumup_payouts")
    assert payout["classification"]=="ELIGIBLE"
    assert payout["watermark_origin"]=="DURABLE_LEDGER"
    assert payout["watermark_timestamp_column"]=="provider_business_date_lower_bound"
    assert payout["records_available"]==2
    assert payout["data_min_at"]=="2026-08-20T00:00:00Z"
    assert payout["data_max_at"]=="2026-08-21T09:30:00Z"


def test_sumup_payout_date_only_is_conservative_day_lower_bound(tmp_path):
    path=database(tmp_path,[row("sumup_payouts", maximum=None, records=None)])
    with sqlite3.connect(path) as db:
        db.execute("create table sumup_payouts(payout_date text not null,raw_json text not null)")
        db.execute("insert into sumup_payouts values(?,?)",("2026-01-01",json.dumps({"date":"2026-01-01"})))
    result=capture_recovery_watermark(path)
    payout=next(x for x in result["sources"] if x["source_id"]=="sumup_payouts")
    assert payout["classification"]=="ELIGIBLE"
    assert payout["data_max_at"]=="2026-01-01T00:00:00Z"


def test_sumup_payout_projection_rejects_import_time_fallback_and_mismatch(tmp_path):
    for suffix, stored, payload in [
        ("missing", "2026-08-21T10:00:00Z", {"amount":"10"}),
        ("mismatch", "2026-08-21", {"payout_date":"2026-08-20"}),
        ("invalid", "2026-08-21T10:00:00Z", {"payout_date":"yesterday"}),
    ]:
        folder=tmp_path/suffix; folder.mkdir()
        path=database(folder,[row("sumup_payouts", maximum=None, records=None)])
        with sqlite3.connect(path) as db:
            db.execute("create table sumup_payouts(payout_date text not null,raw_json text not null)")
            db.execute("insert into sumup_payouts values(?,?)",(stored,json.dumps(payload)))
        result=capture_recovery_watermark(path)
        payout=next(x for x in result["sources"] if x["source_id"]=="sumup_payouts")
        assert payout["classification"]=="UNMEASURABLE"
        assert payout["watermark_origin"]=="DATA_SOURCE_CONTROL_PLANE"
        assert payout["records_available"] is None
        assert payout["data_max_at"] is None


def _marketing_intelligence_schema(db):
    db.execute("create table marketing_audit(event_type text,entity_type text,entity_id text,occurred_at text)")
    db.execute("create table marketing_proposals(proposal_id text,opportunity_id text,created_at text)")
    db.execute("create table marketing_opportunities(opportunity_id text,detected_at text)")
    db.execute("create table marketing_proposal_products(proposal_id text)")
    db.execute("create table marketing_hypotheses(hypothesis_id text,measured_at text)")


def test_marketing_intelligence_projection_counts_only_owned_durable_events(tmp_path):
    path=database(tmp_path,[row("marketing_intelligence", maximum=None, records=None)])
    with sqlite3.connect(path) as db:
        _marketing_intelligence_schema(db)
        # Generic marketing rows must not be attributed to the intelligence source.
        db.execute("insert into marketing_opportunities values('generic-o','2026-08-20T08:00:00Z')")
        db.execute("insert into marketing_proposals values('generic-p','generic-o','2026-08-20T08:00:00Z')")
        db.execute("insert into marketing_proposal_products values('generic-p')")
        db.execute("insert into marketing_audit values('MARKETING_PROPOSAL_CREATED','proposal','generic-p','2026-08-20T08:00:00Z')")
        # This transaction is owned by MarketingIntelligenceService through its atomic audit event.
        db.execute("insert into marketing_opportunities values('intel-o','2026-08-21T09:00:00+00:00')")
        db.execute("insert into marketing_proposals values('intel-p','intel-o','2026-08-21T09:00:01+00:00')")
        db.execute("insert into marketing_proposal_products values('intel-p')")
        db.execute("insert into marketing_audit values('INTELLIGENCE_PROPOSAL_GENERATED','proposal','intel-p','2026-08-21T09:00:02+00:00')")
        # Hypotheses are stored in a source-owned table even when measuring a foreign proposal.
        db.execute("insert into marketing_hypotheses values('hypothesis:1','2026-08-22T10:00:00+00:00')")
    result=capture_recovery_watermark(path)
    source=next(x for x in result["sources"] if x["source_id"]=="marketing_intelligence")
    assert source["classification"]=="ELIGIBLE"
    assert source["watermark_origin"]=="DURABLE_LEDGER"
    assert source["watermark_table"]=="marketing_audit+marketing_hypotheses"
    assert source["watermark_timestamp_column"]=="intelligence_owned_event_time"
    assert source["records_available"]==2
    assert source["data_min_at"]=="2026-08-21T09:00:02Z"
    assert source["data_max_at"]=="2026-08-22T10:00:00Z"


def test_marketing_intelligence_projection_fails_closed_on_incomplete_owned_generation(tmp_path):
    path=database(tmp_path,[row("marketing_intelligence", maximum=None, records=None)])
    with sqlite3.connect(path) as db:
        _marketing_intelligence_schema(db)
        db.execute("insert into marketing_audit values('INTELLIGENCE_PROPOSAL_GENERATED','proposal','missing-p','2026-08-21T09:00:00Z')")
    result=capture_recovery_watermark(path)
    source=next(x for x in result["sources"] if x["source_id"]=="marketing_intelligence")
    assert source["classification"]=="UNMEASURABLE"
    assert source["watermark_origin"]=="DATA_SOURCE_CONTROL_PLANE"
    assert source["records_available"] is None and source["data_max_at"] is None


def test_marketing_intelligence_projection_fails_closed_on_missing_product_or_bad_timestamp(tmp_path):
    for suffix, has_product, measured_at in [
        ("missing-product", False, "2026-08-22T10:00:00Z"),
        ("bad-time", True, "yesterday"),
    ]:
        folder=tmp_path/suffix; folder.mkdir()
        path=database(folder,[row("marketing_intelligence", maximum=None, records=None)])
        with sqlite3.connect(path) as db:
            _marketing_intelligence_schema(db)
            db.execute("insert into marketing_opportunities values('intel-o','2026-08-21T09:00:00Z')")
            db.execute("insert into marketing_proposals values('intel-p','intel-o','2026-08-21T09:00:01Z')")
            if has_product:
                db.execute("insert into marketing_proposal_products values('intel-p')")
            db.execute("insert into marketing_audit values('INTELLIGENCE_PROPOSAL_GENERATED','proposal','intel-p','2026-08-21T09:00:02Z')")
            db.execute("insert into marketing_hypotheses values('hypothesis:1',?)",(measured_at,))
        result=capture_recovery_watermark(path)
        source=next(x for x in result["sources"] if x["source_id"]=="marketing_intelligence")
        assert source["classification"]=="UNMEASURABLE"
        assert source["watermark_origin"]=="DATA_SOURCE_CONTROL_PLANE"


def test_comparison_gaps_and_missing_timestamp():
    def wm(maximum, records=10):
        return {"schema_version":1,"captured_from":"TEST","captured_at":"2026-08-19T12:00:00Z",
                "aggregate_data_max_at":maximum,"confidence":"MEDIUM","coverage":{},"sources":[
                    {"source_id":"sales","classification":"ELIGIBLE","data_max_at":maximum,
                     "last_success_at":maximum,"records_available":records}]}
    equal=compare_recovery_watermarks(wm("2026-08-19T10:00:00Z"),wm("2026-08-19T10:00:00Z"))
    assert equal["observed_rpo_seconds"] == 0 and equal["confidence"] == "HIGH"
    gap=compare_recovery_watermarks(wm("2026-08-19T10:10:00Z"),wm("2026-08-19T10:00:00Z"))
    assert gap["business_data_gap_seconds"] == 600
    unknown=compare_recovery_watermarks(wm(None,11),wm(None,10))
    assert unknown["observed_rpo_seconds"] is None and unknown["record_count_gap"] == 1
    assert unknown["confidence"] == "UNKNOWN"
