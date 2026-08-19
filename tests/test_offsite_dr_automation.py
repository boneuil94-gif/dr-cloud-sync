import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/drcloud-os-offsite-recovery-gameday.yml"
EVIDENCE = ROOT / "docs/evidence/offsite_rpo_coverage_2026-08-19.json"


def test_successful_offsite_backup_triggers_remote_only_recovery():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'workflows: ["DrCloud OS encrypted offsite backup"]' in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "ref: main" in text
    assert "cancel-in-progress: false" in text


def test_recovery_automation_keeps_fail_closed_proof_contract():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "continue-on-error: true" in text
    assert "if: always()" in text
    assert 'test "$STEP_OUTCOME" = success' in text
    assert 'd["restore"]["result"]=="OFFSITE_RESTORE_PROVEN"' in text
    assert 'd["restore"]["app_boot"]=="APP_BOOT_OK"' in text
    assert 'd["restore"]["health"]=="HEALTH_OK"' in text
    assert 'not d["safety"]["local_backup_used_for_restore"]' in text


def test_expanded_rpo_proof_is_bounded_and_sanitized():
    proof = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert proof["result"] == "OFFSITE_SOURCE_AWARE_RPO_COVERAGE_PROVEN"
    assert proof["production_deploy"] == {
        "run_id": 32278318672,
        "run_number": 303,
        "head_sha": "568b928c4647b2714e62fbb5b1a90a016688b500",
        "conclusion": "success",
    }
    assert proof["offsite_backup"]["run_attempt"] == 2
    assert proof["offsite_backup"]["last_result"] == "OFFSITE_REMOTE_CHECK_PROVEN"
    assert proof["remote_recovery"]["run_attempt"] == 2
    assert proof["remote_recovery"]["restore_result"] == "OFFSITE_RESTORE_PROVEN"
    assert proof["remote_recovery"]["app_boot"] == "APP_BOOT_OK"
    assert proof["remote_recovery"]["health"] == "HEALTH_OK"
    assert proof["remote_recovery"]["rpo_method"] == "live_vs_backup_source_watermarks"
    assert proof["remote_recovery"]["observed_rpo_seconds"] == 0
    assert proof["remote_recovery"]["rpo_confidence"] == "MEDIUM"
    assert proof["coverage_change"] == {
        "previous_comparable_sources": 1,
        "current_comparable_sources": 4,
        "previous_unmeasurable_sources": 12,
        "current_unmeasurable_sources": 9,
        "confidence_before": "MEDIUM",
        "confidence_after": "MEDIUM",
        "interpretation": "Coverage materially increased, but incomplete sources prevent a HIGH claim.",
    }
    assert proof["safety"]["local_backup_used_for_restore"] is False
    assert proof["safety"]["live_watermark_used_for_measurement_only"] is True
    assert proof["scores"]["global_strict"] == {"before": 58, "after": 58}
    assert proof["scores"]["deployment"] == {"before": 85, "after": 85}
    forbidden = ("password", "api_key", "authorization", "secret_access", "credential")
    assert not any(word in EVIDENCE.read_text(encoding="utf-8").lower() for word in forbidden)
