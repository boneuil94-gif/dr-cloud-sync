from pathlib import Path


def test_queued_amount_gap_proof_uses_supported_concurrency_and_deploy_lock():
    path = Path('.github/workflows/drcloud-os-finance-amount-gap-proof-queued.yml')
    text = path.read_text(encoding='utf-8')

    assert 'workflows: ["DrCloud OS finance exact-match funnel proof"]' in text
    assert 'group: drcloud-os-finance-amount-gap-proof' in text
    assert 'cancel-in-progress: false' in text
    assert 'queue:' not in text

    lock = 'exec 9>"${DRCLOUD_DEPLOY_LOCK:-/tmp/drcloud-os-deploy.lock}"'
    assert lock in text
    assert 'flock 9' in text
    assert text.index(lock) < text.index('deployed_sha="$(git -C "$repo" rev-parse HEAD)"')
    assert text.index('flock 9') < text.index('docker compose exec -T')

    assert "provider_network_calls':False" in text
    assert "mutations':False" in text
    assert "reference_values_emitted':False" in text
    assert "monetary_values_emitted':False" in text
