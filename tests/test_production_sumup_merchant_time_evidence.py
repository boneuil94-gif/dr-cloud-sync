from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-sumup-merchant-time-evidence.yml')


def _text():
    return WORKFLOW.read_text()


def test_merchant_time_proof_is_post_production_and_sha_pinned():
    text = _text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert '[[ "$REVIEWED_SHA" =~ ^[0-9a-f]{40}$ ]]' in text
    assert '[[ "$deployed_sha" == "$expected_sha" ]]' in text


def test_merchant_time_proof_uses_dedicated_concurrency_and_host_lock():
    text = _text()
    assert 'group: drcloud-os-sumup-merchant-time-evidence' in text
    assert 'cancel-in-progress: false' in text
    source_at = text.index('source "$repo/deploy/ovh/deployment-environment.sh"')
    lock_at = text.index('exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"')
    flock_at = text.index('flock 9')
    sha_at = text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    compose_at = text.index('docker compose exec -T drcloud-os env -i')
    assert source_at < lock_at < flock_at < sha_at < compose_at


def test_merchant_time_proof_is_credential_isolated_and_diagnostic_only():
    text = _text()
    assert 'docker compose exec -T drcloud-os env -i' in text
    assert 'PATH=/usr/local/bin:/usr/bin:/bin' in text
    assert 'DRCLOUD_DATA_DIR=/data' in text
    assert 'EXPECTED_DEPLOYED_SHA="$expected_sha"' in text
    assert 'from dr_cloud_sync.sumup_merchant_time_evidence import sumup_merchant_time_evidence' in text
    assert "evidence.get('evidence_status') != 'MEASURABLE'" in text
    assert "'provider_network_calls':False" in text
    assert "'external_provider_auth':'NONE'" in text
    assert "'mutations':False" in text
    assert "'imported_at_used_as_business_progress':False" in text
    assert "e['rpo_projection_authorized'] is False" in text
    assert "e['current_endpoint_business_timestamp_semantics_proven'] is False" in text


def test_merchant_time_proof_enforces_only_bounded_counts():
    text = _text()
    for key in (
        'merchant_rows','raw_json_objects','raw_json_invalid','updated_at_present_rows',
        'updated_at_aware_rows','updated_at_multiple_location_rows',
        'updated_at_conflicting_location_rows','created_at_present_rows','created_at_aware_rows',
    ):
        assert key in text
    assert "'UPDATED_AT_ALL_ROWS_AWARE'" in text
    assert "'UPDATED_AT_ABSENT'" in text
    assert "'UPDATED_AT_PARTIAL_OR_INVALID'" in text


def test_merchant_time_proof_forbids_sensitive_output_keys():
    text = _text()
    for key in (
        "'raw_json'", "'merchant_code'", "'merchant_id'", "'legal_name'", "'trading_name'",
        "'email'", "'phone'", "'address'", "'iban'", "'account_id'", "'transaction_id'",
        "'payout_id'", "'reference'", "'amount'", "'credential'", "'secret'", "'token'",
        "'authorization'", "'timestamp_value'", "'created_at_value'", "'updated_at_value'", "'imported_at'",
    ):
        assert key in text
    assert "raise SystemExit('SUMUP_MERCHANT_TIME_SENSITIVE_KEY')" in text


def test_merchant_time_proof_uploads_sanitized_artifact_and_indexes_result():
    text = _text()
    assert 'id: upload' in text
    assert 'name: drcloud-sumup-merchant-time-evidence-${{ github.run_id }}' in text
    assert 'path: sumup_merchant_time_evidence.json' in text
    assert 'retention-days: 30' in text
    assert "'workflow':'DrCloud OS SumUp merchant time evidence'" in text
    assert "conclusion='success' if os.environ.get('PROOF_OUTCOME')=='success' and os.environ.get('UPLOAD_OUTCOME')=='success' and os.environ.get('ENFORCE_OUTCOME')=='success' else 'failure'" in text
