from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-finance-unknown-payment-signal-shape-proof.yml')


def _text():
    return WORKFLOW.read_text()


def test_unknown_shape_proof_is_post_production_and_sha_pinned():
    text = _text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert '[[ "$REVIEWED_SHA" =~ ^[0-9a-f]{40}$ ]]' in text
    assert '[[ "$deployed_sha" == "$expected_sha" ]]' in text


def test_unknown_shape_proof_uses_dedicated_concurrency_and_host_lock():
    text = _text()
    assert 'group: drcloud-os-finance-unknown-payment-signal-shape-proof' in text
    assert 'cancel-in-progress: false' in text
    source_at = text.index('source "$repo/deploy/ovh/deployment-environment.sh"')
    lock_at = text.index('exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"')
    flock_at = text.index('flock 9')
    sha_at = text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    compose_at = text.index('docker compose exec -T drcloud-os env -i')
    assert source_at < lock_at < flock_at < sha_at < compose_at


def test_unknown_shape_proof_is_credential_isolated_and_read_only():
    text = _text()
    assert 'docker compose exec -T drcloud-os env -i' in text
    assert 'PATH=/usr/local/bin:/usr/bin:/bin' in text
    assert 'DRCLOUD_DATA_DIR=/data' in text
    assert 'EXPECTED_DEPLOYED_SHA="$expected_sha"' in text
    assert 'from dr_cloud_sync.finance_payment_unknown_shape import upstream_unknown_payment_signal_shape' in text
    assert "evidence.get('evidence_status') != 'MEASURABLE'" in text
    assert "'provider_network_calls':False" in text
    assert "'external_provider_auth':'NONE'" in text
    assert "'mutations':False" in text
    assert "'raw_payment_values_emitted':False" in text
    assert "'sensitive_values_emitted':False" in text


def test_unknown_shape_proof_enforces_bounded_aggregate_contract():
    text = _text()
    for key in (
        'unknown_current_mapping_rows','unknown_payment_type_name_equal','unknown_payment_type_name_different',
        'unknown_description_present','unknown_distinct_payment_type_signatures','unknown_distinct_name_signatures',
        'unknown_distinct_pair_signatures','unknown_largest_payment_type_bucket','unknown_largest_name_bucket',
        'unknown_largest_pair_bucket','unknown_rows_outside_largest_pair_bucket',
    ):
        assert key in text
    assert "'provider_exhaustiveness_inferred'] is False" in text


def test_unknown_shape_proof_forbids_sensitive_output_keys():
    text = _text()
    for key in (
        "'amount'", "'reference'", "'transaction_id'", "'account_id'", "'payout_id'",
        "'payment_id'", "'sale_id'", "'payment_type'", "'name'", "'description'", "'label'", "'value'",
        "'credential'", "'secret'", "'token'", "'authorization'", "'email'", "'phone'",
    ):
        assert key in text
    assert "raise SystemExit('UNKNOWN_PAYMENT_SIGNAL_SHAPE_SENSITIVE_KEY')" in text


def test_unknown_shape_proof_uploads_only_sanitized_artifact_and_indexes_result():
    text = _text()
    assert 'name: drcloud-finance-unknown-payment-signal-shape-evidence-${{ github.run_id }}' in text
    assert 'path: unknown_payment_signal_shape_evidence.json' in text
    assert 'retention-days: 30' in text
    assert "'workflow':'DrCloud OS finance unknown payment signal shape proof'" in text
    assert "conclusion='success' if os.environ.get('PROOF_OUTCOME')=='success' and os.environ.get('ENFORCE_OUTCOME')=='success' else 'failure'" in text
