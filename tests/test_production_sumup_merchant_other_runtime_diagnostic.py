from pathlib import Path


WORKFLOW=Path('.github/workflows/drcloud-os-sumup-merchant-other-runtime-diagnostic.yml')


def text():
    return WORKFLOW.read_text()


def test_other_runtime_diagnostic_is_exact_sha_read_only_and_provider_offline():
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


def test_other_runtime_diagnostic_reads_only_structured_fields_and_emits_no_raw_values():
    body=text()
    assert "SELECT exception_type,category,stage,operation,success FROM connector_diagnostics" in body
    for forbidden in (
        "SELECT message", "SELECT endpoint_path", "SELECT response_excerpt",
        "diagnostic['message']", "diagnostic['endpoint_path']", "diagnostic['response_excerpt']",
    ):
        assert forbidden not in body
    assert "diagnostic['exception_type']" in body
    assert "'raw_error_values_read':False" in body
    assert "'raw_error_values_emitted':False" in body
    assert "'raw_exception_type_emitted':False" in body
    assert "'endpoint_paths_emitted':False" in body
    assert "'credentials_or_pii_emitted':False" in body


def test_other_runtime_family_is_closed_and_code_grounded():
    body=text()
    for family in (
        'TRANSPORT_PROTOCOL_RUNTIME','TLS_SOCKET_RUNTIME','SQLITE_API_RUNTIME','PYTHON_CODE_RUNTIME',
        'SUMUP_DOMAIN_RUNTIME','SCHEMA_RUNTIME','PRIOR_KNOWN_FAMILY','UNCLASSIFIED_OTHER','MISSING',
    ):
        assert family in body
    for exception_type in (
        'RemoteDisconnected','IncompleteRead','BadStatusLine','SSLError','CertificateError',
        'ConnectionResetError','ProgrammingError','InterfaceError','NameError','ImportError',
        'SumUpError','SchemaDriftError',
    ):
        assert exception_type in body
    assert "'provider_exhaustiveness_inferred':False" in body
    assert "'rpo_projection_authorized':False" in body
    assert "'imported_at_used_as_business_progress':False" in body
