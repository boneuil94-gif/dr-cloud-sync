import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "docs/evidence/sumup_payout_recovery_2026-08-19.json"


def test_sumup_payout_recovery_production_evidence_is_bounded_and_truthful():
    proof = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert proof["result"] == "SUMUP_PAYOUT_RECOVERY_PROVEN"
    assert proof["production"] == {
        "run_id": 32289798227,
        "run_number": 317,
        "head_sha": "7e540772c1d1ff35ba40776bb77b478f4c05da2d",
        "conclusion": "success",
    }
    recovery = proof["recovery_proof"]
    assert recovery["run_id"] == 32289896556
    assert recovery["event"] == "workflow_run"
    assert recovery["head_sha"] == proof["production"]["head_sha"]
    assert recovery["artifact_id"] == 9379057643
    assert recovery["artifact_sha256"] == "a59e977c6ae7feeb2d9725da359ad39c9a0c9b571eaf16954ca5b91902d51bb7"

    assert proof["source"]["before_status"] == "ERROR"
    assert proof["source"]["after_status"] == "CONNECTED"
    assert proof["source"]["after_records_available"] == 760
    assert proof["ledger"] == {
        "before_records": 328,
        "after_records": 760,
        "nondecreasing": True,
    }
    assert proof["job"] == {"job_id": "sync_sumup_payouts", "status": "SUCCEEDED"}
    assert proof["safety"]["provider_method"] == "GET_ONLY"
    assert proof["safety"]["provider_mutations"] is False
    assert proof["safety"]["raw_provider_payload_in_evidence"] is False
    assert proof["safety"]["credentials_in_evidence"] is False
    assert proof["scope"]["provider_authority_total_proven"] is False
    assert proof["scope"]["end_to_end_financial_reconciliation_proven"] is False
    assert proof["scores"]["global_strict"] == {"before": 58, "after": 58}
    assert proof["scores"]["deployment"] == {"before": 85, "after": 85}

    forbidden = ("password", "api_key", "authorization", "secret_access", "credential")
    text = EVIDENCE.read_text(encoding="utf-8").lower()
    assert not any(word in text for word in forbidden)
