from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-finance-upstream-payment-population-proof.yml')


def _text():
    return WORKFLOW.read_text()


def test_upstream_payment_population_proof_is_post_production_and_sha_pinned():
    text = _text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert '[[ "$REVIEWED_SHA" =~ ^[0-9a-f]{40}$ ]]' in text
    assert '[[ "$deployed_sha" == "$expected_sha" ]]' in text


def test_upstream_payment_population_proof_uses_dedicated_concurrency_and_host_lock():
    text = _text()
    assert 'group: drcloud-os-finance-upstream-payment-population-proof' in text
    assert 'cancel-in-progress: false' in text
    source_at = text.index('source "$repo/deploy/ovh/deployment-environment.sh"')
    lock_at = text.index('exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"')
    flock_at = text.index('flock 9')
    sha_at = text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    compose_at = text.index('docker compose exec -T drcloud-os env -i')
    assert source_at < lock_at < flock_at < sha_at < compose_at


def test_upstream_payment_population_proof_is_credential_isolated_and_read_only():
    text = _text()
    assert 'docker compose exec -T drcloud-os env -i' in text
    assert 'PATH=/usr/local/bin:/usr/bin:/bin' in text
    assert 'DRCLOUD_DATA_DIR=/data' in text
    assert 'EXPECTED_DEPLOYED_SHA="$expected_sha"' in text
    assert 'from dr_cloud_sync.finance_upstream_population import upstream_payment_population' in text
    assert "evidence.get('evidence_status') != 'MEASURABLE'" in text
    assert "'provider_network_calls':False" in text
    assert "'external_provider_auth':'NONE'" in text
    assert "'mutations':False" in text
    assert "'sensitive_values_emitted':False" in text


def test_upstream_payment_population_proof_enforces_bounded_contract():
    text = _text()
    for key in (
        'shopcaisse_sales',
        'shopcaisse_sales_with_any_payment',
        'shopcaisse_payments',
        'shopcaisse_payments_card',
        'shopcaisse_payments_non_card_known',
        'shopcaisse_payments_unknown_or_missing_type',
        'shopcaisse_payments_quality_valid',
        'shopcaisse_payments_quality_non_valid',
        'shopcaisse_payments_card_and_valid',
        'shopcaisse_payments_card_and_non_valid',
        'shopcaisse_payment_exposure',
        'tickets_observed_presence',
        'payment_objects_observed_presence',
    ):
        assert key in text
    assert "{'EXPOSED','API_NOT_EXPOSED','NOT_OBSERVED','UNKNOWN'}" in text
    assert "{'ZERO','NONZERO','UNKNOWN'}" in text
    assert "'provider_exhaustiveness_inferred'] is False" in text


def test_upstream_payment_population_proof_forbids_sensitive_output_keys():
    text = _text()
    for key in (
        "'amount'", "'reference'", "'transaction_id'", "'account_id'", "'payout_id'",
        "'payment_id'", "'sale_id'", "'payment_type'", "'name'", "'description'",
        "'credential'", "'secret'", "'token'", "'authorization'", "'email'", "'phone'",
    ):
        assert key in text
    assert "raise SystemExit('UPSTREAM_PAYMENT_POPULATION_SENSITIVE_KEY')" in text


def test_upstream_payment_population_proof_uploads_only_sanitized_artifact_and_indexes_result():
    text = _text()
    assert 'name: drcloud-finance-upstream-payment-population-evidence-${{ github.run_id }}' in text
    assert 'path: upstream_payment_population_evidence.json' in text
    assert 'retention-days: 30' in text
    assert "'workflow':'DrCloud OS finance upstream payment population proof'" in text
    assert "conclusion='success' if os.environ.get('PROOF_OUTCOME')=='success' and os.environ.get('ENFORCE_OUTCOME')=='success' else 'failure'" in text
