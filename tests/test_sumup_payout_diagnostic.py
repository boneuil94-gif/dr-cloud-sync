from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "deploy/ovh/sumup-payout-diagnostic.sh"
WORKFLOW_PATH = ROOT / ".github/workflows/drcloud-os-sumup-payout-diagnostic.yml"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")


def test_sumup_payout_diagnostic_is_read_only_and_payload_free():
    assert "mode=ro" in SCRIPT
    assert 'SOURCE_ID = "sumup_payouts"' in SCRIPT
    assert '"database_read_only": True' in SCRIPT
    assert '"provider_network_calls": False' in SCRIPT
    assert '"external_provider_auth": "NONE"' in SCRIPT
    assert '"mutations": False' in SCRIPT
    assert '"unstructured_text_selected": False' in SCRIPT
    assert '"provider_payload_selected": False' in SCRIPT
    assert '"pagination_state_selected": False' in SCRIPT
    assert '"message"' in SCRIPT and '"response_excerpt"' in SCRIPT and '"cursor"' in SCRIPT
    assert "SELECT {','.join(SAFE_DIAGNOSTIC_COLUMNS)}" in SCRIPT
    assert "urllib" not in SCRIPT
    assert "requests." not in SCRIPT


def test_sumup_payout_diagnostic_exposes_only_structured_failure_facts():
    for field in (
        '"stage"', '"endpoint_path"', '"http_status"', '"category"',
        '"exception_type"', '"occurred_at"', '"duration_ms"', '"success"',
    ):
        assert field in SCRIPT
    assert "SUMUP_PAYOUT_DIAGNOSTIC_SENSITIVE_KEY" in SCRIPT
    assert "SUMUP_PAYOUT_DIAGNOSTIC_SENSITIVE_VALUE" in SCRIPT
    assert '"current_failure_count"' in SCRIPT
    assert '"latest_current"' in SCRIPT


def test_sumup_payout_diagnostic_workflow_is_auto_run_and_fail_closed():
    assert "name: DrCloud OS SumUp payout diagnostic" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    assert "push:" in WORKFLOW
    assert "branches: [main]" in WORKFLOW
    assert "deploy/ovh/sumup-payout-diagnostic.sh" in WORKFLOW
    assert "environment: production" in WORKFLOW
    assert "contents: read" in WORKFLOW
    assert "issues: write" in WORKFLOW
    assert "continue-on-error: true" in WORKFLOW
    assert "Enforce diagnostic contract" in WORKFLOW
    assert 'test "$DIAGNOSTIC_OUTCOME" = success' in WORKFLOW
    assert 'p["result"] == "SUMUP_PAYOUT_DIAGNOSTIC_CAPTURED"' in WORKFLOW
    assert "drcloud-sumup-payout-diagnostic-${{ github.run_id }}" in WORKFLOW
    assert "issues/170/comments" in WORKFLOW
    assert 'conclusion = "success" if os.environ.get("ENFORCE_OUTCOME") == "success" else "failure"' in WORKFLOW


def test_sumup_payout_diagnostic_shell_syntax():
    result = subprocess.run(["bash", "-n", SCRIPT_PATH], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
