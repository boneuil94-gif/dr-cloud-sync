from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "deploy/ovh/sumup-payout-recovery-proof.sh"
WORKFLOW_PATH = ROOT / ".github/workflows/drcloud-os-sumup-payout-recovery-proof.yml"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")


def test_recovery_proof_only_retries_when_source_is_error():
    assert 'if before["status"] == "ERROR":' in SCRIPT
    assert 'action = "NOT_NEEDED"' in SCRIPT
    assert 'action = "RETRY_EXECUTED"' in SCRIPT
    assert 'hub.run(JOB_ID, operation, manual=True)' in SCRIPT
    assert 'SOURCE_ID = "sumup_payouts"' in SCRIPT
    assert 'JOB_ID = "sync_sumup_payouts"' in SCRIPT


def test_recovery_proof_uses_read_only_provider_contract_and_sanitized_failure():
    assert '"provider_method": "GET_ONLY"' in SCRIPT
    assert '"provider_mutations": False' in SCRIPT
    assert '"raw_provider_payload_in_evidence": False' in SCRIPT
    assert '"credentials_in_evidence": False' in SCRIPT
    assert 'diagnostic.get("category")' in SCRIPT
    assert 'diagnostic.get("stage")' in SCRIPT
    assert 'diagnostic.get("http_status")' in SCRIPT
    assert '"message"' not in SCRIPT
    assert 'response_excerpt' not in SCRIPT


def test_recovery_proof_requires_connected_source_and_nondecreasing_ledger():
    assert 'after["status"] == "CONNECTED"' in SCRIPT
    assert 'after_count >= before_count' in SCRIPT
    assert '"before_records_available"' in SCRIPT
    assert '"after_records_available"' in SCRIPT
    assert '"SUMUP_PAYOUT_RECOVERY_PROVEN"' in SCRIPT
    assert '"SUMUP_PAYOUT_RECOVERY_FAILED"' in SCRIPT


def test_recovery_workflow_runs_after_successful_main_production_and_fails_closed():
    assert 'workflows: ["DrCloud OS Production"]' in WORKFLOW
    assert "types: [completed]" in WORKFLOW
    assert "github.event.workflow_run.conclusion == 'success'" in WORKFLOW
    assert "github.event.workflow_run.head_branch == 'main'" in WORKFLOW
    assert "github.event_name == 'workflow_dispatch'" in WORKFLOW
    assert "github.event.workflow_run.head_sha || github.sha" in WORKFLOW
    assert "ref: main" not in WORKFLOW
    assert "REVIEWED_SHA:" in WORKFLOW
    assert '"head_sha": os.environ["REVIEWED_SHA"]' in WORKFLOW
    assert "trigger_run_id" in WORKFLOW
    assert "continue-on-error: true" in WORKFLOW
    assert 'test "$RECOVERY_OUTCOME" = success' in WORKFLOW
    assert 'p["result"] == "SUMUP_PAYOUT_RECOVERY_PROVEN"' in WORKFLOW
    assert 'p["source"]["after_status"] == "CONNECTED"' in WORKFLOW
    assert 'p["source"]["after_records_available"] == p["ledger"]["after_records"]' in WORKFLOW
    assert "issues/170/comments" in WORKFLOW


def test_recovery_proof_shell_syntax():
    result = subprocess.run(["bash", "-n", SCRIPT_PATH], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
