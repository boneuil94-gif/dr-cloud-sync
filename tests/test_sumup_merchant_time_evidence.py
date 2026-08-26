import json
import sqlite3

from dr_cloud_sync.sumup_merchant_time_evidence import sumup_merchant_time_evidence


def _db(tmp_path):
    path = tmp_path / "merchant.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE sumup_merchants(
            merchant_code TEXT PRIMARY KEY,
            raw_json TEXT NOT NULL,
            imported_at TEXT NOT NULL)"""
        )
    return path


def _insert(path, code, payload, imported_at="2099-12-31T23:59:59Z"):
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO sumup_merchants(merchant_code,raw_json,imported_at) VALUES(?,?,?)",
            (code, json.dumps(payload), imported_at),
        )


def test_missing_ledger_is_fail_closed(tmp_path):
    result = sumup_merchant_time_evidence(tmp_path / "missing.sqlite")
    assert result["evidence_status"] == "UNMEASURABLE"
    assert result["reason"] == "REQUIRED_LEDGER_MISSING"
    assert result["rpo_projection_authorized"] is False


def test_incomplete_schema_is_fail_closed(tmp_path):
    path = tmp_path / "bad.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sumup_merchants(merchant_code TEXT PRIMARY KEY, imported_at TEXT)")
    result = sumup_merchant_time_evidence(path)
    assert result["evidence_status"] == "UNMEASURABLE"
    assert result["reason"] == "REQUIRED_SCHEMA_INCOMPLETE"


def test_top_level_updated_at_is_counted_without_emitting_value(tmp_path):
    path = _db(tmp_path)
    _insert(
        path,
        "SECRET-MERCHANT",
        {
            "merchant_code": "SECRET-MERCHANT",
            "updated_at": "2026-08-25T12:34:56Z",
            "created_at": "2021-01-01T00:00:00+00:00",
            "legal_name": "Sensitive Merchant Name",
        },
    )
    result = sumup_merchant_time_evidence(path)
    assert result["evidence_status"] == "MEASURABLE"
    assert result["candidate_readiness"] == "UPDATED_AT_ALL_ROWS_AWARE"
    assert result["counts"] == {
        "merchant_rows": 1,
        "raw_json_objects": 1,
        "raw_json_invalid": 0,
        "updated_at_present_rows": 1,
        "updated_at_aware_rows": 1,
        "updated_at_multiple_location_rows": 0,
        "updated_at_conflicting_location_rows": 0,
        "created_at_present_rows": 1,
        "created_at_aware_rows": 1,
    }
    assert result["rpo_projection_authorized"] is False
    assert result["current_endpoint_business_timestamp_semantics_proven"] is False
    assert result["safety"]["imported_at_used_as_business_progress"] is False
    text = repr(result)
    assert "2026-08-25" not in text
    assert "2099-12-31" not in text
    assert "SECRET-MERCHANT" not in text
    assert "Sensitive Merchant Name" not in text


def test_nested_profile_updated_at_is_supported_as_shape_only(tmp_path):
    path = _db(tmp_path)
    _insert(
        path,
        "MC",
        {"merchant_profile": {"merchant_code": "MC", "updated_at": "2026-08-25T12:34:56+02:00"}},
    )
    result = sumup_merchant_time_evidence(path)
    assert result["candidate_readiness"] == "UPDATED_AT_ALL_ROWS_AWARE"
    assert result["counts"]["updated_at_present_rows"] == 1
    assert result["counts"]["updated_at_aware_rows"] == 1


def test_conflicting_duplicate_locations_are_not_promoted(tmp_path):
    path = _db(tmp_path)
    _insert(
        path,
        "MC",
        {
            "updated_at": "2026-08-25T12:00:00Z",
            "merchant_profile": {"updated_at": "2026-08-25T13:00:00Z"},
        },
    )
    result = sumup_merchant_time_evidence(path)
    assert result["candidate_readiness"] == "UPDATED_AT_CONFLICTING"
    assert result["counts"]["updated_at_multiple_location_rows"] == 1
    assert result["counts"]["updated_at_conflicting_location_rows"] == 1
    assert result["rpo_projection_authorized"] is False


def test_imported_at_alone_never_becomes_business_progress(tmp_path):
    path = _db(tmp_path)
    _insert(path, "MC", {"merchant_profile": {"merchant_code": "MC"}})
    result = sumup_merchant_time_evidence(path)
    assert result["candidate_readiness"] == "UPDATED_AT_ABSENT"
    assert result["counts"]["updated_at_present_rows"] == 0
    assert result["counts"]["updated_at_aware_rows"] == 0
    assert result["safety"]["imported_at_used_as_business_progress"] is False


def test_invalid_or_naive_timestamp_never_counts_as_aware(tmp_path):
    path = _db(tmp_path)
    _insert(path, "A", {"updated_at": "yesterday"})
    _insert(path, "B", {"updated_at": "2026-08-25T12:34:56"})
    result = sumup_merchant_time_evidence(path)
    assert result["candidate_readiness"] == "UPDATED_AT_PARTIAL_OR_INVALID"
    assert result["counts"]["updated_at_present_rows"] == 2
    assert result["counts"]["updated_at_aware_rows"] == 0


def test_invalid_raw_json_is_bounded_and_sanitized(tmp_path):
    path = _db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO sumup_merchants(merchant_code,raw_json,imported_at) VALUES(?,?,?)",
            ("MC", "not-json-secret", "2026-08-25T00:00:00Z"),
        )
    result = sumup_merchant_time_evidence(path)
    assert result["candidate_readiness"] == "RAW_PAYLOAD_INVALID"
    assert result["counts"]["raw_json_invalid"] == 1
    assert "not-json-secret" not in repr(result)
