from pathlib import Path


def test_finance_match_funnel_production_proof_is_pinned_read_only_and_sanitized():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/drcloud-os-finance-exact-match-funnel-proof.yml").read_text()
    helper = (root / "src/dr_cloud_sync/finance_match_funnel.py").read_text()

    assert 'workflows: ["DrCloud OS Production"]' in workflow
    assert "group: drcloud-os-production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "REVIEWED_SHA" in workflow and "github.event.workflow_run.head_sha" in workflow
    assert 'ref: ${{ env.REVIEWED_SHA }}' in workflow
    assert "EXPECTED_DEPLOYED_SHA" in workflow
    assert workflow.index("deployment-environment.sh") < workflow.index("docker compose")
    assert "exact_match_funnel(path,bank_provider='qonto')" in workflow
    assert "provider_exhaustiveness_inferred" in workflow
    assert "provider_network_calls" in workflow and "False" in workflow
    assert "external_provider_auth" in workflow and "NONE" in workflow
    assert "row_level_identifiers_emitted" in workflow
    assert "reference_values_emitted" in workflow
    assert "free_form_banking_data_emitted" in workflow
    assert "OVH_SSH_PRIVATE_KEY" in workflow
    assert "QONTO_CREDENTIAL" not in workflow
    assert "SUMUP_API_KEY" not in workflow
    assert "PRESTASHOP_API_KEY" not in workflow
    assert "SHOPCAISSE_API_KEY" not in workflow
    assert "mode=ro" in helper
    assert "provider_exhaustiveness_inferred" in helper
