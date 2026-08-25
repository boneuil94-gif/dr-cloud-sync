from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-finance-payment-type-observation-proof.yml')


def _text():
    return WORKFLOW.read_text()


def test_payment_type_observation_proof_is_post_production_and_sha_pinned():
    text = _text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert '[[ "$REVIEWED_SHA" =~ ^[0-9a-f]{40}$ ]]' in text
    assert '[[ "$deployed_sha" == "$expected_sha" ]]' in text


def test_payment_type_observation_proof_uses_dedicated_concurrency_and_host_lock():
    text = _text()
    assert 'group: drcloud-os-finance-payment-type-observation-proof' in text
    assert 'cancel-in-progress: false' in text
    source_at = text.index('source "$repo/deploy/ovh/deployment-environment.sh"')
    lock_at = text.index('exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"')
    flock_at = text.index('flock 9')
    sha_at = text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    compose_at = text.index('docker compose exec -T drcloud-os env -i')
    assert source_at < lock_at < flock_at < sha_at < compose_at


def test_payment_type_observation_proof_is_credential_isolated_and_read_only():
    text = _text()
    assert 'docker compose exec -T drcloud-os env -i' in text
    assert 'PATH=/usr/local/bin:/usr/bin:/bin' in text
    assert 'DRCLOUD_DATA_DIR=/data' in text
    assert 'EXPECTED_DEPLOYED_SHA="$expected_sha"' in text
    assert 'from dr_cloud_sync.finance_payment_type_observation import upstream_payment_type_observation' in text
    assert "evidence.get('evidence_status') != 'MEASURABLE'" in text
    assert "'provider_network_calls':False" in text
    assert "'external_provider_auth':'NONE'" in text
    assert "'mutations':False" in text
    assert "'raw_payment_values_emitted':False" in text
    assert "'sensitive_values_emitted':False" in text


def test_payment_type_observation_proof_enforces_bounded_contract():
    text = _text()
    for key in (
        'shopcaisse_payments','canonical_card','canonical_known_non_card','canonical_unknown_or_other',
        'raw_signal_none','raw_signal_any','raw_payment_type_present','raw_name_present','raw_description_present',
        'unknown_with_no_raw_signal','unknown_with_raw_signal','mapping_rule_missing','mapping_rule_unknown_label',
        'mapping_rule_recognized_payment_type','mapping_rule_recognized_name','mapping_rule_recognized_description',
        'mapping_rule_other_or_legacy','mapping_version_current','mapping_version_other_or_legacy',
        'unknown_current_mapping_version','unknown_other_or_legacy_mapping_version',
    ):
        assert key in text
    assert "'provider_exhaustiveness_inferred'] is False" in text


def test_payment_type_observation_proof_forbids_sensitive_output_keys():
    text = _text()
    for key in (
        "'amount'", "'reference'", "'transaction_id'", "'account_id'", "'payout_id'",
        "'payment_id'", "'sale_id'", "'payment_type'", "'name'", "'description'",
        "'credential'", "'secret'", "'token'", "'authorization'", "'email'", "'phone'",
    ):
        assert key in text
    assert "raise SystemExit('PAYMENT_TYPE_OBSERVATION_SENSITIVE_KEY')" in text


def test_payment_type_observation_proof_uploads_only_sanitized_artifact_and_indexes_result():
    text = _text()
    assert 'name: drcloud-finance-payment-type-observation-evidence-${{ github.run_id }}' in text
    assert 'path: payment_type_observation_evidence.json' in text
    assert 'retention-days: 30' in text
    assert "'workflow':'DrCloud OS finance payment type observation proof'" in text
    assert "conclusion='success' if os.environ.get('PROOF_OUTCOME')=='success' and os.environ.get('ENFORCE_OUTCOME')=='success' else 'failure'" in text
