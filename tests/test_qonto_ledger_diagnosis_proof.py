from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/ovh/production-qonto-ledger-diagnosis-proof.sh"
WORKFLOW = ROOT / ".github/workflows/drcloud-os-qonto-ledger-diagnosis-proof.yml"


def test_qonto_diagnosis_script_is_local_read_only_and_sha_pinned():
    text = SCRIPT.read_text()
    assert "EXPECTED_DEPLOYED_SHA" in text
    assert "git -C \"$repo\" rev-parse HEAD" in text
    assert "?mode=ro" in text
    assert "provider_network_calls\": False" in text
    assert "external_provider_auth\": \"NONE\"" in text
    assert "provider_exhaustiveness_inferred\": False" in text
    assert "diagnosis_scope\": \"LOCAL_LEDGER_ONLY\"" in text
    assert "NO_LOCAL_QONTO_BOOKED_CREDITS" in text
    assert "LOCAL_QONTO_BOOKED_CREDITS_PRESENT" in text
    assert "requests." not in text and "urlopen" not in text


def test_qonto_diagnosis_does_not_emit_row_identifiers_or_reference_values():
    text = SCRIPT.read_text()
    assert '"row_level_identifiers_emitted": False' in text
    assert '"reference_values_emitted": False' in text
    assert '"transaction_id", "account_id", "counterparty", "reference"' in text
    assert "SELECT direction,status,booked_at,imported_at,reference" in text
    assert '"direction_counts": dict(sorted(directions.items()))' in text
    assert '"status_counts": dict(sorted(statuses.items()))' in text
    assert '"credit_status_counts": dict(sorted(credit_statuses.items()))' in text


def test_qonto_diagnosis_workflow_triggers_only_after_green_main_production_or_manual():
    text = WORKFLOW.read_text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "ref: ${{ env.REVIEWED_SHA }}" in text
    assert "EXPECTED_DEPLOYED_SHA='$REVIEWED_SHA'" in text
    assert "PRODUCTION_QONTO_LEDGER_DIAGNOSIS_CAPTURED" in text
    assert "provider_exhaustiveness_inferred\"] is False" in text
    assert "DrCloud OS Qonto local ledger diagnosis proof" in text
