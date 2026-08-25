from pathlib import Path


def _workflow():
    root = Path(__file__).parents[1]
    return (root / '.github/workflows/drcloud-os-finance-upstream-funnel-proof.yml').read_text()


def test_upstream_funnel_proof_is_post_production_and_sha_pinned():
    workflow = _workflow()
    assert 'name: DrCloud OS finance upstream settlement funnel proof' in workflow
    assert 'workflows: ["DrCloud OS Production"]' in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in workflow
    assert 'ref: ${{ env.REVIEWED_SHA }}' in workflow
    assert 'EXPECTED_DEPLOYED_SHA=' in workflow
    assert 'UPSTREAM_FUNNEL_DEPLOYED_SHA_MISMATCH' in workflow


def test_upstream_funnel_proof_holds_deploy_lock_through_read_only_capture():
    workflow = _workflow()
    assert 'group: drcloud-os-finance-upstream-funnel-proof' in workflow
    assert 'cancel-in-progress: false' in workflow
    source = workflow.index('source "$repo/deploy/ovh/deployment-environment.sh"')
    lock = workflow.index('flock 9')
    sha_check = workflow.index('UPSTREAM_FUNNEL_DEPLOYED_SHA_MISMATCH')
    compose = workflow.index('docker compose exec -T drcloud-os env -i')
    capture = workflow.index('upstream_settlement_funnel(path)')
    assert source < lock < sha_check < compose < capture
    assert 'DRCLOUD_DEPLOY_LOCK' in workflow
    assert 'DRCLOUD_DATA_DIR=/data' in workflow
    assert 'PATH=/usr/local/bin:/usr/bin:/bin' in workflow


def test_upstream_funnel_proof_is_sanitized_and_fail_closed():
    workflow = _workflow()
    assert "evidence.get('evidence_status') != 'MEASURABLE'" in workflow
    assert 'UPSTREAM_FUNNEL_EVIDENCE_UNAVAILABLE' in workflow
    assert "'provider_exhaustiveness_inferred'] is False" in workflow
    assert "'sale_to_qonto_coverage_claimed'] is False" in workflow
    assert "'downstream_qonto_requires_separate_exact_reconciliation_proof'] is True" in workflow
    assert "'provider_network_calls':False" in workflow
    assert "'external_provider_auth':'NONE'" in workflow
    assert "'mutations':False" in workflow
    assert "'row_level_ids_emitted':False" in workflow
    assert "'business_timestamps_emitted':False" in workflow
    assert "'provider_values_emitted':False" in workflow
    assert "'sensitive_values_emitted':False" in workflow
    assert 'retention-days: 30' in workflow
    for forbidden in ('SHOPCAISSE_API_KEY', 'QONTO_CREDENTIAL', 'SUMUP_API_KEY', 'PRESTASHOP_API_KEY'):
        assert forbidden not in workflow


def test_upstream_funnel_artifact_shape_is_bounded_to_aggregates():
    workflow = _workflow()
    for name in (
        'shopcaisse_sales_with_eligible_card_payment',
        'eligible_card_payments',
        'payments_without_matched_sumup_transaction',
        'payments_with_unique_matched_sumup_transaction',
        'payments_with_multiple_matched_sumup_transactions',
        'unique_transaction_payments_without_payout_membership',
        'unique_transaction_payments_with_unique_payout_membership',
        'unique_transaction_payments_with_multiple_payout_memberships',
        'payment_to_unique_sumup_transaction_ratio',
        'unique_transaction_to_unique_payout_ratio',
    ):
        assert name in workflow
    assert "'payment_id'" in workflow
    assert "'sale_id'" in workflow
    assert "'transaction_id'" in workflow
    assert "'payout_id'" in workflow
    assert "UPSTREAM_FUNNEL_SENSITIVE_KEY" in workflow
    assert "result':'PRODUCTION_UPSTREAM_SETTLEMENT_FUNNEL_CAPTURED'" in workflow
