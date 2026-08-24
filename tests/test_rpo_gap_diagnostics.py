import sqlite3

from dr_cloud_sync.rpo_gap_diagnostics import source_rpo_gap_diagnostics


COLUMNS = """source_id text, source_type text, provider text, status text, enabled integer,
last_success_at text, stale_after_seconds integer, data_min_at text, data_max_at text,
records_available integer"""


def test_rpo_gap_diagnostics_emits_only_bounded_gap_facts(tmp_path):
    path = tmp_path / "rpo.db"
    with sqlite3.connect(path) as db:
        db.execute(f"create table data_sources ({COLUMNS})")
        db.executemany(
            "insert into data_sources values (?,?,?,?,?,?,?,?,?,?)",
            [
                ("good", "SALES", "provider-fixture-value", "CONNECTED", 1,
                 "2026-08-24T10:00:00Z", 3600, "2026-08-01T00:00:00Z",
                 "2026-08-24T09:59:00Z", 10),
                ("missing", "OTHER", "provider-fixture-value", "CONNECTED", 1,
                 "2026-08-24T10:00:00Z", 3600, None, None, 5),
                ("badts", "OTHER", "provider-fixture-value", "CONNECTED", 1,
                 "2026-08-24T10:00:00Z", 3600, None, "yesterday", 3),
            ],
        )
    result = source_rpo_gap_diagnostics(path)
    assert result["evidence_scope"] == "LOCAL_RECOVERY_WATERMARK_ONLY"
    assert result["evidence_status"] == "MEASURABLE"
    assert result["provider_exhaustiveness_inferred"] is False
    assert result["timestamps_emitted"] is False
    assert result["sensitive_values_emitted"] is False
    assert result["gap_count"] == 2
    assert result["gaps"] == [
        {"source_id": "badts", "classification": "INVALID_TIMESTAMP",
         "watermark_origin": "DATA_SOURCE_CONTROL_PLANE", "records_state": "NONZERO",
         "data_max_state": "PRESENT"},
        {"source_id": "missing", "classification": "UNMEASURABLE",
         "watermark_origin": "DATA_SOURCE_CONTROL_PLANE", "records_state": "NONZERO",
         "data_max_state": "MISSING"},
    ]
    text = repr(result)
    assert "2026-" not in text
    assert "provider-fixture-value" not in text


def test_rpo_gap_diagnostics_ignores_neutral_sources(tmp_path):
    path = tmp_path / "neutral.db"
    with sqlite3.connect(path) as db:
        db.execute(f"create table data_sources ({COLUMNS})")
        db.executemany(
            "insert into data_sources values (?,?,?,?,?,?,?,?,?,?)",
            [
                ("off", "OTHER", "provider", "DISABLED", 0, None, 3600, None, None, None),
                ("empty", "OTHER", "provider", "CONNECTED", 1, None, 3600, None, None, 0),
                ("none", "OTHER", "provider", "NOT_CONFIGURED", 1, None, 3600, None, None, None),
            ],
        )
    result = source_rpo_gap_diagnostics(path)
    assert result["evidence_status"] == "MEASURABLE"
    assert result["gap_count"] == 0
    assert result["gaps"] == []


def test_rpo_gap_diagnostics_preserves_unavailable_watermark_state(tmp_path):
    result = source_rpo_gap_diagnostics(tmp_path / "missing.db")
    assert result["evidence_status"] == "UNAVAILABLE"
    assert result["source_count"] is None
    assert result["gap_count"] is None
    assert result["gaps"] == []
    assert result["provider_exhaustiveness_inferred"] is False
