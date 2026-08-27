from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-sumup-merchant-watermark-production-proof.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_runs_only_after_successful_main_production_and_pins_exact_sha():
    text = _text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert 'EXPECTED_DEPLOYED_SHA' in text
    assert 'SUMUP_MERCHANT_WATERMARK_DEPLOYED_SHA_MISMATCH' in text


def test_holds_deploy_lock_and_observes_sqlite_read_only():
    text = _text()
    assert 'source "$repo/deploy/ovh/deployment-environment.sh"' in text
    assert 'DRCLOUD_DEPLOY_LOCK' in text
    assert 'flock 9' in text
    assert '?mode=ro' in text
    assert "SELECT status,records_available,data_max_at FROM data_sources WHERE source_id='sumup_merchant'" in text
    assert "job_id='sync_sumup_merchant' AND job_type='SUMUP_MERCHANT'" in text
    assert "SELECT status,job_id FROM data_hub_sync_runs WHERE job_id='sync_sumup_merchant' ORDER BY run_id DESC LIMIT 1" in text
    assert "evidence['source_status']=='CONNECTED'" in text
    assert "evidence['job_status']=='SUCCEEDED'" in text
    assert "assert e['source_status']=='CONNECTED'" in text
    assert "assert e['job_status']=='SUCCEEDED'" in text
    assert 'provider_network_calls' in text and "'provider_network_calls':False" in text
    assert "'external_provider_auth':'NONE'" in text
    assert "'provider_mutations':False" in text


def test_proves_durable_provider_watermark_without_emitting_values():
    text = _text()
    assert "evidence['records_available']>0" in text
    assert "evidence['merchant_rows']>0" in text
    assert "evidence['merchant_rows_with_provider_updated_at']==evidence['merchant_rows']" in text
    assert "evidence['data_max_at_persisted']" in text
    assert "evidence['data_max_at_matches_provider_updated_at']" in text
    assert "source['data_max_at']==merchant['max_updated']" in text
    assert "'timestamp_values_emitted':False" in text
    assert "'merchant_identifiers_emitted':False" in text
    assert "'provider_payloads_emitted':False" in text
    assert "'credentials_or_pii_emitted':False" in text
    assert "'provider_exhaustiveness_inferred':False" in text
    assert "'imported_at_used_as_business_progress':False" in text


def test_proof_does_not_run_provider_or_mutate_business_state():
    text = _text()
    forbidden = (
        'SumUpProvider(',
        '.merchant()',
        'sync_sumup_merchant(',
        'data_hub.run(',
        'UPDATE data_sources',
        'INSERT INTO sumup_merchants',
        'DELETE FROM sumup_merchants',
    )
    for token in forbidden:
        assert token not in text


def test_upload_and_index_require_full_success():
    text = _text()
    assert 'id: upload' in text
    assert 'test "$PROOF_OUTCOME" = success' in text
    assert 'test "$UPLOAD_OUTCOME" = success' in text
    assert "conclusion='success' if os.environ.get('PROOF_OUTCOME')=='success' and os.environ.get('UPLOAD_OUTCOME')=='success' and os.environ.get('ENFORCE_OUTCOME')=='success' else 'failure'" in text
    assert "'workflow':'DrCloud OS SumUp merchant watermark production proof'" in text
