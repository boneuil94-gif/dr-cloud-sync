from pathlib import Path


def test_group_fee_gap_production_proof_is_pinned_locked_and_sanitized():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/drcloud-os-finance-group-fee-gap-proof.yml").read_text()

    assert 'workflows: ["DrCloud OS Production"]' in workflow
    assert "group: drcloud-os-finance-group-fee-gap-proof" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}" in workflow
    assert "ref: ${{ env.REVIEWED_SHA }}" in workflow

    source_index = workflow.index('source "$repo/deploy/ovh/deployment-environment.sh"')
    lock_index = workflow.index('exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"')
    flock_index = workflow.index("flock 9")
    sha_index = workflow.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    compose_index = workflow.index("docker compose exec -T")
    assert source_index < lock_index < flock_index < sha_index < compose_index

    assert "from dr_cloud_sync.finance_group_fee_gap import group_fee_gap_funnel" in workflow
    assert "group_fee_gap_funnel(path,bank_provider='qonto')" in workflow
    assert "PRODUCTION_FINANCE_GROUP_FEE_GAP_CAPTURED" in workflow
    assert "provider_exhaustiveness_inferred'] is False" in workflow
    assert "database_read_only':True" in workflow
    assert "provider_network_calls':False" in workflow
    assert "external_provider_auth':'NONE'" in workflow
    assert "mutations':False" in workflow
    assert "row_level_identifiers_emitted':False" in workflow
    assert "reference_values_emitted':False" in workflow
    assert "free_form_banking_data_emitted':False" in workflow
    assert "monetary_values_emitted':False" in workflow


def test_group_fee_gap_production_proof_clears_container_credentials_before_python():
    workflow = (
        Path(__file__).parents[1]
        / ".github/workflows/drcloud-os-finance-group-fee-gap-proof.yml"
    ).read_text()
    assert "docker compose exec -T drcloud-os env -i" in workflow
    assert "PATH=/usr/local/bin:/usr/bin:/bin" in workflow
    assert "DRCLOUD_DATA_DIR=/data" in workflow
    assert 'EXPECTED_DEPLOYED_SHA="$expected_sha"' in workflow
    assert "env -i" in workflow[: workflow.index("python - <<'PY'")]

    forbidden = (
        "QONTO_CREDENTIAL",
        "SUMUP_API_KEY",
        "SUMUP_MERCHANT_CODE",
        "PRESTASHOP_API_KEY",
        "SHOPCAISSE_API_KEY",
        "OFFSITE_RESTIC_PASSWORD",
        "OFFSITE_S3_ACCESS_KEY_ID",
        "OFFSITE_S3_SECRET_ACCESS_KEY",
    )
    assert not any(name in workflow for name in forbidden)
