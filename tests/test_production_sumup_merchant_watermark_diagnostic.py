from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-sumup-merchant-watermark-diagnostic.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_runs_after_watermark_proof_completion_and_pins_exact_sha():
    text = _text()
    assert 'workflows: ["DrCloud OS SumUp merchant watermark production proof"]' in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert 'SUMUP_MERCHANT_DIAGNOSTIC_DEPLOYED_SHA_MISMATCH' in text
    assert 'workflows: ["DrCloud OS Production"]' not in text


def test_holds_deploy_lock_and_reads_sqlite_only():
    text = _text()
    assert 'source "$repo/deploy/ovh/deployment-environment.sh"' in text
    assert 'DRCLOUD_DEPLOY_LOCK' in text
    assert 'flock 9' in text
    assert '?mode=ro' in text
    assert "ORDER BY run_id DESC LIMIT 1" in text
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


def test_emits_bounded_diagnosis_without_business_timestamp_values():
    text = _text()
    for diagnosis in (
        'SOURCE_MISSING',
        'SOURCE_NOT_CONNECTED',
        'JOB_MISSING',
        'JOB_NOT_SUCCEEDED',
        'RUN_MISSING',
        'LATEST_RUN_NOT_SUCCEEDED',
        'RECORDS_ZERO',
        'LEDGER_EMPTY',
        'PROVIDER_UPDATED_AT_INCOMPLETE',
        'DATA_MAX_AT_MISSING',
        'DATA_MAX_AT_MISMATCH',
        'READY',
    ):
        assert diagnosis in text
    assert "'timestamp_values_emitted': False" in text
    assert "'merchant_identifiers_emitted': False" in text
    assert "'provider_payloads_emitted': False" in text
    assert "'credentials_or_pii_emitted': False" in text
    assert "'provider_exhaustiveness_inferred': False" in text
    assert "'rpo_projection_authorized': False" in text
    assert "'imported_at_used_as_business_progress': False" in text


def test_diagnostic_reports_only_boolean_states_and_aggregate_counts():
    text = _text()
    assert "'records_available_nonzero': records_available > 0" in text
    assert "'merchant_rows_nonzero': merchant_rows > 0" in text
    assert "'all_merchant_rows_have_provider_updated_at': all_rows_have_provider_updated_at" in text
    assert "'data_max_at_persisted': data_max_at_persisted" in text
    assert "'data_max_at_matches_provider_updated_at': data_max_at_matches_provider_updated_at" in text
    assert "'records_available': records_available" in text
    assert "'merchant_rows': merchant_rows" in text
    assert "'merchant_rows_with_provider_updated_at': merchant_rows_with_provider_updated_at" in text


def test_failed_capture_or_contract_cannot_be_indexed_as_success():
    text = _text()
    assert 'id: capture' in text
    assert 'continue-on-error: true' in text
    assert 'id: upload' in text
    assert 'id: enforce' in text
    assert 'if: always()' in text
    assert 'test "$CAPTURE_OUTCOME" = success' in text
    assert 'test "$UPLOAD_OUTCOME" = success' in text
    assert 'CAPTURE_OUTCOME: ${{ steps.capture.outcome }}' in text
    assert 'UPLOAD_OUTCOME: ${{ steps.upload.outcome }}' in text
    assert 'ENFORCE_OUTCOME: ${{ steps.enforce.outcome }}' in text
    assert "os.environ.get('CAPTURE_OUTCOME') == 'success'" in text
    assert "os.environ.get('UPLOAD_OUTCOME') == 'success'" in text
    assert "os.environ.get('ENFORCE_OUTCOME') == 'success'" in text
    assert "'conclusion': conclusion" in text
    assert "'workflow': 'DrCloud OS SumUp merchant watermark diagnostic'" in text
