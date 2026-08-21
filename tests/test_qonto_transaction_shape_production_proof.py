from pathlib import Path


def test_qonto_transaction_shape_proof_is_sha_pinned_read_only_and_sanitized():
    root=Path(__file__).parents[1]
    script=(root/'deploy/ovh/production-qonto-transaction-shape-proof.sh').read_text()
    workflow=(root/'.github/workflows/drcloud-os-qonto-transaction-shape-proof.yml').read_text()
    assert 'EXPECTED_DEPLOYED_SHA' in script and 'rev-parse HEAD' in script
    assert 'qonto_local_transaction_shape' in script
    assert 'deployment-environment.sh' in script
    assert 'provider_network_calls' in script and "'external_provider_auth':'NONE'" in script
    assert "'mutations':False" in script and "'row_level_identifiers_emitted':False" in script
    assert "'reference_values_emitted':False" in script
    assert 'workflow_run' in workflow and 'DrCloud OS Production' in workflow
    assert 'ref: ${{ env.REVIEWED_SHA }}' in workflow
    assert 'provider_exhaustiveness_inferred' in workflow
    forbidden=('QONTO_CREDENTIAL','QONTO_API_KEY','Authorization:','transactions/details','curl https://thirdparty.qonto.com')
    for token in forbidden:
        assert token not in script


def test_qonto_transaction_shape_evidence_never_emits_reference_values_or_free_text():
    root=Path(__file__).parents[1]
    script=(root/'deploy/ovh/production-qonto-transaction-shape-proof.sh').read_text()
    assert "'reference'" in script  # forbidden-key scanner, not an emitted value
    assert "'label'" in script and "'counterparty'" in script
    assert 'reference_values_emitted' in script
