from pathlib import Path


WORKFLOW=Path('.github/workflows/drcloud-os-sumup-merchant-exception-family-diagnostic.yml')


def text():
    return WORKFLOW.read_text()


def test_exception_family_diagnostic_is_exact_sha_read_only_and_provider_offline():
    body=text()
    assert "workflow_run:" in body
    assert 'workflows: ["DrCloud OS SumUp merchant watermark production proof"]' in body
    assert "github.event.workflow_run.conclusion == 'failure'" in body
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in body
    assert '[[ "$deployed_sha" == "$expected_sha" ]]' in body
    assert 'DRCLOUD_DEPLOY_LOCK' in body
    assert '?mode=ro' in body
    assert "'provider_network_calls':False" in body
    assert "'external_provider_auth':'NONE'" in body
    assert "'provider_mutations':False" in body


def test_exception_family_diagnostic_reads_only_structured_exception_type_not_error_text():
    body=text()
    assert "SELECT exception_type,category,stage,operation,success FROM connector_diagnostics" in body
    assert "SELECT message" not in body
    assert "SELECT endpoint_path" not in body
    assert "SELECT response_excerpt" not in body
    assert "diagnostic['message']" not in body
    assert "diagnostic['endpoint_path']" not in body
    assert "diagnostic['response_excerpt']" not in body
    assert "diagnostic['exception_type']" in body
    assert "'raw_error_values_read':False" in body
    assert "'raw_error_values_emitted':False" in body
    assert "'raw_exception_type_emitted':False" in body


def test_exception_family_output_is_bounded_and_keeps_rpo_safety_flags():
    body=text()
    for family in (
        'TYPE_ERROR','KEY_ERROR','VALUE_ERROR','ATTRIBUTE_ERROR','RUNTIME_ERROR',
        'ASSERTION_ERROR','JSON_DECODE_ERROR','HTTP_ERROR','URL_ERROR','TIMEOUT_ERROR',
        'SQLITE_OPERATIONAL_ERROR','SQLITE_INTEGRITY_ERROR','SQLITE_DATABASE_ERROR',
        'OS_ERROR','OTHER','MISSING',
    ):
        assert family in body
    assert "'provider_exhaustiveness_inferred':False" in body
    assert "'rpo_projection_authorized':False" in body
    assert "'imported_at_used_as_business_progress':False" in body
    assert "'credentials_or_pii_emitted':False" in body
    assert "'merchant_identifiers_emitted':False" in body
    assert "'run_identifiers_emitted':False" in body
    assert "'timestamp_values_emitted':False" in body
