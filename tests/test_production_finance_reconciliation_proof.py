from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1]
SCRIPT = (ROOT / "deploy/ovh/production-finance-reconciliation-proof.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/drcloud-os-finance-reconciliation-proof.yml").read_text(encoding="utf-8")


def test_finance_reconciliation_proof_is_read_only_aggregate_only_and_fail_closed():
    assert "reconcile_sumup_payouts_to_bank" in SCRIPT
    assert 'bank_provider="Qonto"' in SCRIPT
    assert "mode=ro" not in SCRIPT  # read-only is enforced by the imported reconciliation service
    assert '"database_read_only": True' in SCRIPT
    assert '"mutations": False' in SCRIPT
    assert '"provider_network_calls": False' in SCRIPT
    assert '"external_provider_auth": "NONE"' in SCRIPT
    assert '"row_level_identifiers_emitted": False' in SCRIPT
    assert '"provider_authority_totals_proven": False' in SCRIPT
    assert '"end_to_end_funnel_proven": False' in SCRIPT
    assert '"fuzzy_fallback": False' in SCRIPT
    assert '"bank_credit_single_use": True' in SCRIPT
    assert 'result.get("rows")' not in SCRIPT
    assert '"payout_id"' in SCRIPT and '"bank_transaction_id"' in SCRIPT and '"reference"' in SCRIPT
    assert "FINANCE_RECONCILIATION_EVIDENCE_SENSITIVE_KEY" in SCRIPT


def test_finance_reconciliation_proof_requires_exact_deployed_sha():
    assert 'EXPECTED_DEPLOYED_SHA' in SCRIPT
    assert '^[0-9a-f]{40}$' in SCRIPT
    assert 'git -C "$repo" rev-parse HEAD' in SCRIPT
    assert 'FINANCE_RECONCILIATION_DEPLOYED_SHA_MISMATCH' in SCRIPT
    assert SCRIPT.index("deployment-environment.sh") < SCRIPT.index('docker "$compose_subcommand"')


def test_finance_reconciliation_proof_validates_aggregate_invariants():
    assert "FINANCE_RECONCILIATION_COUNTS_INCONSISTENT" in SCRIPT
    assert "FINANCE_RECONCILIATION_RATIO_INCONSISTENT" in SCRIPT
    assert "FINANCE_RECONCILIATION_NO_DATA_INVALID" in SCRIPT
    assert "FINANCE_RECONCILIATION_UNMEASURABLE_INVALID" in SCRIPT
    assert '"status": status' in SCRIPT
    assert '"coverage_ratio": result.get("coverage_ratio")' in SCRIPT


def test_finance_reconciliation_workflow_is_pinned_to_successful_production_sha():
    assert "name: DrCloud OS finance reconciliation proof" in WORKFLOW
    assert 'workflows: ["DrCloud OS Production"]' in WORKFLOW
    assert "github.event.workflow_run.conclusion == 'success'" in WORKFLOW
    assert "github.event.workflow_run.head_branch == 'main'" in WORKFLOW
    assert "github.event.workflow_run.head_sha" in WORKFLOW
    assert "Check out exact reviewed SHA" in WORKFLOW
    assert "EXPECTED_DEPLOYED_SHA='$REVIEWED_SHA'" in WORKFLOW
    assert "environment: production" in WORKFLOW
    assert "continue-on-error: true" in WORKFLOW
    assert "Enforce captured evidence" in WORKFLOW
    assert 'test "$PROOF_OUTCOME" = success' in WORKFLOW
    assert "drcloud-finance-reconciliation-evidence-${{ github.run_id }}" in WORKFLOW
    assert "issues/170/comments" in WORKFLOW
    assert "group: drcloud-os-production" in WORKFLOW


def test_finance_reconciliation_proof_shell_syntax():
    result = subprocess.run(
        ["bash", "-n", ROOT / "deploy/ovh/production-finance-reconciliation-proof.sh"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
