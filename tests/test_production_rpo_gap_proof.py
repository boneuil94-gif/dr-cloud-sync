from pathlib import Path


def workflow_text():
    return Path('.github/workflows/drcloud-os-rpo-gap-proof.yml').read_text()


def test_rpo_gap_proof_is_sha_pinned_locked_and_read_only():
    text = workflow_text()
    assert 'workflows: ["DrCloud OS Production"]' in text
    assert 'github.event.workflow_run.conclusion == \'success\'' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert 'source "$repo/deploy/ovh/deployment-environment.sh"' in text
    assert 'DRCLOUD_DEPLOY_LOCK' in text
    assert text.index('flock 9') < text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    assert text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"') < text.index('docker compose exec -T drcloud-os env -i')
    assert 'group: drcloud-os-rpo-gap-proof' in text
    assert 'cancel-in-progress: false' in text
    assert 'DRCLOUD_DATA_DIR=/data' in text
    assert "source_rpo_gap_diagnostics(path)" in text
    assert "evidence.get('evidence_status') != 'MEASURABLE'" in text


def test_rpo_gap_proof_keeps_runtime_environment_sanitized():
    text = workflow_text()
    assert 'docker compose exec -T drcloud-os env -i' in text
    assert 'PATH=/usr/local/bin:/usr/bin:/bin' in text
    assert 'EXPECTED_DEPLOYED_SHA="$expected_sha"' in text
    assert "'provider_network_calls':False" in text
    assert "'external_provider_auth':'NONE'" in text
    assert "'mutations':False" in text
    assert "'timestamps_emitted':False" in text
    assert "'provider_values_emitted':False" in text
    assert "'row_level_business_data_emitted':False" in text
    assert "'sensitive_values_emitted':False" in text


def test_rpo_gap_proof_enforces_bounded_measurable_artifact():
    text = workflow_text()
    assert "assert e['evidence_status']=='MEASURABLE'" in text
    assert "assert e['provider_exhaustiveness_inferred'] is False" in text
    assert "assert e['timestamps_emitted'] is False" in text
    assert "assert e['sensitive_values_emitted'] is False" in text
    assert "assert e['gap_count']==len(e['gaps'])" in text
    assert 'retention-days: 30' in text
    for forbidden_literal in ('last_success_at', 'data_max_at', 'data_min_at', 'cursor', 'credential'):
        assert f"'{forbidden_literal}'" in text
