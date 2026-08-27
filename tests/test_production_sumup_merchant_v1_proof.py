from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-sumup-merchant-v1-production-proof.yml')


def _text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def test_workflow_is_post_production_exact_sha_and_deployment_locked():
    text = _text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert 'deployment-environment.sh' in text
    assert 'DRCLOUD_DEPLOY_LOCK' in text
    assert 'flock 9' in text
    assert 'deployed_sha="$(git -C "$repo" rev-parse HEAD)"' in text
    assert '[[ "$deployed_sha" == "$expected_sha" ]]' in text
    assert '-e EXPECTED_DEPLOYED_SHA="$expected_sha"' in text
    assert 'group: drcloud-os-sumup-merchant-v1-production-proof' in text


def test_workflow_uses_existing_read_only_provider_contract_and_no_mutation():
    text = _text()
    assert 'provider=SumUpProvider(' in text
    assert 'payload=provider.merchant()' in text
    assert "endpoint_contract':'GET_V1_MERCHANT_BY_CONFIGURED_CODE'" in text
    assert "provider_mutations':False" in text
    forbidden_calls = (
        '.post(', '.put(', '.patch(', '.delete(',
        'requests.post', 'requests.put', 'requests.patch', 'requests.delete',
        'urllib.request.urlopen(Request(',
    )
    for token in forbidden_calls:
        assert token not in text


def test_workflow_requires_aware_created_and_updated_at_without_emitting_values():
    text = _text()
    assert "created=payload.get('created_at')" in text
    assert "updated=payload.get('updated_at')" in text
    assert "created_at_present'" in text
    assert "created_at_timezone_aware_rfc3339'" in text
    assert "updated_at_present'" in text
    assert "updated_at_timezone_aware_rfc3339'" in text
    assert "SUMUP_MERCHANT_V1_TIMESTAMP_CONTRACT_UNPROVEN" in text
    assert "timestamp_values_emitted':False" in text
    assert "merchant_identifiers_emitted':False" in text
    assert "credentials_emitted':False" in text
    assert "raw_provider_values_emitted':False" in text
    assert "rpo_projection_authorized':False" in text
    assert "provider_exhaustiveness_inferred':False" in text
    assert "print(created)" not in text
    assert "print(updated)" not in text
    assert "print(payload)" not in text


def test_workflow_upload_and_index_fail_closed():
    text = _text()
    assert 'id: upload' in text
    assert 'UPLOAD_OUTCOME: ${{ steps.upload.outcome }}' in text
    assert 'test "$PROOF_OUTCOME" = success' in text
    assert 'test "$UPLOAD_OUTCOME" = success' in text
    assert "os.environ.get('PROOF_OUTCOME')=='success'" in text
    assert "os.environ.get('UPLOAD_OUTCOME')=='success'" in text
    assert "os.environ.get('ENFORCE_OUTCOME')=='success'" in text
    assert "workflow':'DrCloud OS SumUp merchant v1 production proof'" in text


def test_artifact_contract_forbids_sensitive_field_names():
    text = _text()
    for key in (
        'merchant_code','merchant_id','legal_name','trading_name','email','phone',
        'address','iban','account_id','credential','secret','token','authorization',
        'created_at_value','updated_at_value','raw_json','payload',
    ):
        assert repr(key) in text
