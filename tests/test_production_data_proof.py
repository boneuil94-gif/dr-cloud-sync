from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1]
SCRIPT = (ROOT / "deploy/ovh/production-data-proof.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/drcloud-os-production-data-proof.yml").read_text(encoding="utf-8")


def test_production_data_proof_is_read_only_and_does_not_overclaim_coverage():
    assert "mode=ro" in SCRIPT
    assert '"database_read_only": True' in SCRIPT
    assert '"mutations": False' in SCRIPT
    assert '"provider_network_calls": False' in SCRIPT
    assert '"external_provider_auth": "NONE"' in SCRIPT
    assert "UNKNOWN_AUTHORITY_TOTAL" in SCRIPT
    assert '"authoritative_coverage_proven": False' in SCRIPT
    assert '"end_to_end_match_rate": None' in SCRIPT
    assert '"end_to_end_status": "NOT_PROVEN"' in SCRIPT
    assert "urllib" not in SCRIPT
    assert "requests." not in SCRIPT


def test_production_data_proof_selects_only_sanitized_source_fields():
    expected = (
        '"source_id", "source_type", "provider", "status", "enabled",\n'
        '        "last_success_at", "stale_after_seconds", "data_min_at", "data_max_at",\n'
        '        "records_available", "rows_imported",'
    )
    assert expected in SCRIPT
    assert "safe_source_columns" in SCRIPT
    assert '"cursor", "last_error", "raw", "password", "secret", "token"' in SCRIPT
    assert "PRODUCTION_DATA_EVIDENCE_SENSITIVE_KEY" in SCRIPT
    assert "PRODUCTION_DATA_EVIDENCE_SENSITIVE_VALUE" in SCRIPT


def test_production_data_proof_workflow_is_fail_closed_and_connector_discoverable():
    assert "name: DrCloud OS production data proof" in WORKFLOW
    assert "environment: production" in WORKFLOW
    assert "contents: read" in WORKFLOW
    assert "issues: write" in WORKFLOW
    assert "continue-on-error: true" in WORKFLOW
    assert "Enforce captured evidence" in WORKFLOW
    assert 'test "$PROOF_OUTCOME" = success' in WORKFLOW
    assert "drcloud-production-data-evidence-${{ github.run_id }}" in WORKFLOW
    assert "issues/170/comments" in WORKFLOW
    assert '"conclusion": conclusion' in WORKFLOW


def test_production_data_proof_shell_syntax():
    result = subprocess.run(
        ["bash", "-n", ROOT / "deploy/ovh/production-data-proof.sh"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
