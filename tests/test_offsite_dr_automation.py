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


def test_expanded_rpo_and_automatic_chain_proof_is_bounded_and_sanitized():
    proof = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert proof["result"] == "OFFSITE_SOURCE_AWARE_RPO_COVERAGE_AND_AUTOMATION_PROVEN"
    assert proof["production_deploy"] == {
        "run_id": 32279523916,
        "run_number": 305,
        "head_sha": "f3d9f3fc2c9997bb157040284400e83255aade99",
        "conclusion": "success",
    }

    manual = proof["bounded_manual_proof"]
    assert manual["offsite_backup"]["run_attempt"] == 2
    assert manual["offsite_backup"]["last_result"] == "OFFSITE_REMOTE_CHECK_PROVEN"
    assert manual["remote_recovery"]["run_attempt"] == 2
    assert manual["remote_recovery"]["restore_result"] == "OFFSITE_RESTORE_PROVEN"
    assert manual["remote_recovery"]["rpo_method"] == "live_vs_backup_source_watermarks"
    assert manual["remote_recovery"]["observed_rpo_seconds"] == 0
    assert manual["remote_recovery"]["rpo_confidence"] == "MEDIUM"

    automatic = proof["automatic_chain"]
    assert automatic["result"] == "AUTOMATIC_OFFSITE_DR_CHAIN_PROVEN"
    assert automatic["trigger_contract"] == "SUCCESSFUL_MAIN_OFFSITE_BACKUP_TRIGGERS_REMOTE_ONLY_RECOVERY"
    assert automatic["offsite_backup"]["run_id"] == 32263186935
    assert automatic["offsite_backup"]["run_attempt"] == 3
    assert automatic["offsite_backup"]["artifact_id"] == 9375336270
    assert automatic["offsite_backup"]["last_result"] == "OFFSITE_REMOTE_CHECK_PROVEN"
    assert automatic["remote_recovery"]["run_id"] == 32279754025
    assert automatic["remote_recovery"]["run_number"] == 3
    assert automatic["remote_recovery"]["event"] == "workflow_run"
    assert automatic["remote_recovery"]["head_branch"] == "main"
    assert automatic["remote_recovery"]["head_sha"] == "f3d9f3fc2c9997bb157040284400e83255aade99"
    assert automatic["remote_recovery"]["artifact_id"] == 9375359086
    assert automatic["remote_recovery"]["restore_result"] == "OFFSITE_RESTORE_PROVEN"
    assert automatic["remote_recovery"]["app_boot"] == "APP_BOOT_OK"
    assert automatic["remote_recovery"]["health"] == "HEALTH_OK"
    assert automatic["remote_recovery"]["observed_rpo_seconds"] == 620.805
    assert automatic["remote_recovery"]["rpo_confidence"] == "MEDIUM"
    assert automatic["remote_recovery"]["comparable_sources"] == 4
    assert automatic["remote_recovery"]["unmeasurable_sources"] == 9

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
    assert "9 source watermarks remain unmeasurable" in proof["remaining_limitations"]
    forbidden = ("password", "api_key", "authorization", "secret_access", "credential")
    assert not any(word in EVIDENCE.read_text(encoding="utf-8").lower() for word in forbidden)
