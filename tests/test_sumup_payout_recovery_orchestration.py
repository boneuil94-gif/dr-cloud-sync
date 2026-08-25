from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-sumup-payout-recovery-proof.yml')


def _text():
    return WORKFLOW.read_text()


def test_sumup_payout_recovery_uses_dedicated_github_concurrency():
    text = _text()
    assert 'group: drcloud-os-sumup-payout-recovery-proof' in text
    assert 'group: drcloud-os-production' not in text
    assert 'cancel-in-progress: false' in text


def test_sumup_payout_recovery_holds_deploy_lock_before_sha_check_and_proof():
    text = _text()
    lock = text.index('flock 9')
    sha_check = text.index('[[ "$deployed_sha" == "$expected_sha" ]]')
    proof = text.index('"$proof_script"', sha_check)
    assert lock < sha_check < proof
    assert 'source "$repo/deploy/ovh/deployment-environment.sh"' in text


def test_sumup_payout_recovery_is_pinned_to_reviewed_sha():
    text = _text()
    assert 'REVIEWED_SHA:' in text
    assert 'ref: ${{ env.REVIEWED_SHA }}' in text
    assert "EXPECTED_DEPLOYED_SHA='$REVIEWED_SHA'" in text
    assert 'SUMUP_PAYOUT_RECOVERY_DEPLOYED_SHA_MISMATCH' in text
