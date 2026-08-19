import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "docs/evidence/production_data_source_truth_2026-08-19.json"


def test_production_source_semantics_evidence_is_exact_and_bounded():
    proof = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert proof["result"] == "PRODUCTION_DATA_SOURCE_SEMANTICS_PROVEN"
    assert proof["provenance"] == {
        "run_id": 32282037993,
        "run_number": 3,
        "head_sha": "4b4a5ea4faa9c10169a4b8650da5b8f3a4fbfd8f",
        "conclusion": "success",
        "artifact_id": 9376187997,
        "artifact_sha256": "32ba63d85d4b34fcbe5556ee0d4054eb39a8ad3a34065f037fc34917dad2f792",
    }
    plane = proof["source_control_plane"]
    assert plane["source_count"] == 19
    assert plane["configured_sources"] == 13
    assert plane["operationally_wired_sources"] == 11
    assert plane["directly_wired_sources"] == 8
    assert plane["derived_sources"] == 3
    assert plane["derived_source_ids"] == ["sumup_chargebacks", "sumup_fees", "sumup_refunds"]
    assert plane["unwired_sources"] == 2
    assert plane["unwired_source_ids"] == ["sumup_merchant", "sumup_readers"]
    assert plane["authoritative_coverage_proven"] is False
    assert plane["sumup_payouts_status"] == "ERROR"

    funnel = proof["finance_funnel"]
    assert funnel["known_local_stage_counts"] == 5
    assert funnel["stages"] == {
        "sales": 811,
        "payments": 753,
        "sumup_transactions": 11110,
        "sumup_payouts": 328,
        "qonto_transactions": 2754,
    }
    assert funnel["authority"] == "LOCAL_LEDGER_COUNT"
    assert funnel["end_to_end_match_rate"] is None
    assert funnel["end_to_end_status"] == "NOT_PROVEN"
    assert proof["safety"] == {
        "database_read_only": True,
        "mutations": False,
        "provider_network_calls": False,
        "external_provider_auth": "NONE",
    }
    assert proof["scores"]["global_strict"] == {"before": 58, "after": 58}
    assert proof["scores"]["deployment"] == {"before": 85, "after": 85}


def test_production_source_semantics_evidence_contains_no_sensitive_material():
    text = EVIDENCE.read_text(encoding="utf-8").lower()
    forbidden = ("password", "api_key", "authorization", "secret_access", "credential", "bearer ")
    assert not any(word in text for word in forbidden)
