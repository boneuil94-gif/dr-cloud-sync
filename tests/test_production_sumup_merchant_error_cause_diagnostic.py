from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-sumup-merchant-error-cause-diagnostic.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_runs_after_watermark_proof_within_chain_limit_and_pins_exact_sha():
    text = _text()
    assert 'workflows: ["DrCloud OS SumUp merchant watermark production proof"]' in text
    assert 'DrCloud OS SumUp merchant watermark diagnostic' not in text.split('permissions:')[0]
    assert 'REVIEWED_SHA: ${{ github.event.workflow_run.head_sha }}' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert 'EXPECTED_DEPLOYED_SHA' in text
    assert 'SUMUP_MERCHANT_ERROR_CAUSE_DEPLOYED_SHA_MISMATCH' in text


def test_holds_deploy_lock_and_reads_only_sqlite_control_plane():
    text = _text()
    assert 'source "$repo/deploy/ovh/deployment-environment.sh"' in text
    assert 'DRCLOUD_DEPLOY_LOCK' in text
    assert 'flock 9' in text
    assert '?mode=ro' in text
    assert "SELECT run_id,status FROM data_hub_sync_runs WHERE job_id='sync_sumup_merchant' ORDER BY run_id DESC LIMIT 1" in text
    assert "AND job_id='sync_sumup_merchant' AND run_id=? ORDER BY diagnostic_id DESC LIMIT 1" in text
    for token in (
        'SumUpProvider(', '.merchant()', 'sync_sumup_merchant(', 'data_hub.run(',
        'UPDATE data_sources', 'UPDATE sync_jobs', 'INSERT INTO sumup_merchants',
        'DELETE FROM sumup_merchants',
    ):
        assert token not in text


def test_correlates_cause_to_latest_merchant_run_without_emitting_run_id():
    text = _text()
    assert "if run is not None:" in text
    assert "(run['run_id'],)).fetchone()" in text
    assert "'latest_run_diagnostic_present':diagnostic is not None" in text
    assert "'run_identifiers_emitted':False" in text
    assert "'run_id':run" not in text


def test_emits_only_bounded_error_cause_fields():
    text = _text()
    assert "'category':bounded(diagnostic['category'],categories)" in text
    assert "'stage_family':stage_family(diagnostic['stage'])" in text
    assert "'http_status_class':http_class(diagnostic['http_status'])" in text
    assert "'operation_family':operation_family(diagnostic['operation'])" in text
    assert "'raw_error_values_emitted':False" in text
    assert "'timestamp_values_emitted':False" in text
    assert "'merchant_identifiers_emitted':False" in text
    assert "'endpoint_paths_emitted':False" in text
    assert "'provider_payloads_emitted':False" in text
    assert "'credentials_or_pii_emitted':False" in text
    assert "'provider_exhaustiveness_inferred':False" in text
    assert "'rpo_projection_authorized':False" in text
    assert "'imported_at_used_as_business_progress':False" in text


def test_auth_scope_classification_is_explicit_without_exposing_status_value():
    text = _text()
    assert "if code in (401,403): return 'AUTH_401_403'" in text
    assert "if code == 404: return 'NOT_FOUND_404'" in text
    assert "if code == 429: return 'RATE_LIMIT_429'" in text
    assert "if 500 <= code < 600: return 'SERVER_5XX'" in text


def test_index_success_requires_capture_upload_and_enforce():
    text = _text()
    assert 'id: capture' in text
    assert 'id: upload' in text
    assert 'id: enforce' in text
    assert "conclusion='success' if os.environ.get('CAPTURE_OUTCOME')=='success' and os.environ.get('UPLOAD_OUTCOME')=='success' and os.environ.get('ENFORCE_OUTCOME')=='success' else 'failure'" in text
    assert "'workflow':'DrCloud OS SumUp merchant error cause diagnostic'" in text
