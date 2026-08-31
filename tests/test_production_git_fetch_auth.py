from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / '.github/workflows/drcloud-os-production.yml'
UPDATE = ROOT / 'deploy/ovh/update.sh'


def test_production_stages_reviewed_updater_and_passes_read_token_only_over_stdin():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'permissions:\n  contents: read' in text
    assert 'deploy/ovh/update.sh' in text
    assert ':/tmp/drcloud-update-$DEPLOY_SHA.sh' in text
    assert 'GITHUB_FETCH_TOKEN: ${{ github.token }}' in text
    assert 'test -n "$GITHUB_FETCH_TOKEN"' in text
    assert "printf '%s\\n' \"$GITHUB_FETCH_TOKEN\" |" in text
    assert 'DRCLOUD_REPO_DIR=/opt/drcloud-os DRCLOUD_GITHUB_TOKEN_STDIN=1' in text
    assert '/bin/bash /tmp/drcloud-update-$DEPLOY_SHA.sh' in text
    assert 'rm -f /tmp/drcloud-update-$DEPLOY_SHA.sh' in text


def test_updater_uses_ephemeral_extraheader_without_persisting_credentials():
    text = UPDATE.read_text(encoding='utf-8')
    assert 'repo="${DRCLOUD_REPO_DIR:-}"' in text
    assert 'DRCLOUD_GITHUB_TOKEN_STDIN' in text
    assert 'IFS= read -r github_token' in text
    assert "printf 'x-access-token:%s' \"$github_token\"" in text
    assert "GIT_CONFIG_KEY_0='http.https://github.com/.extraheader'" in text
    assert 'GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"' in text
    assert 'git -C "$repo" fetch --no-tags origin main' in text
    assert 'unset github_token auth' in text

    forbidden = (
        'git remote set-url',
        'credential.helper store',
        '.git-credentials',
        'https://x-access-token:',
        '@github.com',
    )
    for token in forbidden:
        assert token not in text


def test_updater_keeps_exact_sha_and_pre_fetch_backup_contract():
    text = UPDATE.read_text(encoding='utf-8')
    backup = text.index('"$repo/deploy/ovh/backup.sh"')
    fetch = text.index('fetch_reviewed_main', text.index('"$repo/deploy/ovh/backup.sh"'))
    ancestry = text.index('merge-base --is-ancestor "$target" origin/main')
    checkout = text.index('checkout --detach "$target"')
    assert backup < fetch < ancestry < checkout
