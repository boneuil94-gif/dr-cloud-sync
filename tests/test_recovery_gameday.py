import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/drcloud-os-recovery-gameday.yml"
SCRIPT = ROOT / "deploy/ovh/recovery-gameday.sh"


def _run_python_runtime_selection(tmp_path, runtimes):
    for runtime in runtimes:
        executable = tmp_path / runtime
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
    text = SCRIPT.read_text()
    selection = "\n".join(text.splitlines()[3:8])
    return subprocess.run(
        ["/bin/bash", "-c", f'{selection}\nprintf "%s" "$PYTHON_BIN"'],
        env={**os.environ, "PATH": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_host_python_runtime_prefers_python3_and_falls_back_or_fails_closed(tmp_path):
    preferred = _run_python_runtime_selection(tmp_path, ("python", "python3"))
    assert preferred.returncode == 0
    assert preferred.stdout == str(tmp_path / "python3")

    (tmp_path / "python3").unlink()
    fallback = _run_python_runtime_selection(tmp_path, ("python",))
    assert fallback.returncode == 0
    assert fallback.stdout == str(tmp_path / "python")

    (tmp_path / "python").unlink()
    missing = _run_python_runtime_selection(tmp_path, ())
    assert missing.returncode == 127
    assert missing.stderr.strip() == "RECOVERY_PYTHON_RUNTIME_MISSING"


def test_host_python_calls_always_use_selected_runtime():
    text = SCRIPT.read_text()
    assert 'PYTHON_BIN="$(command -v python3 || command -v python || true)"' in text
    assert not re.search(r"(?m)^\s*(?:if\s+)?python(?:\s|$)", text)
    assert text.count('"$PYTHON_BIN"') == 8


def test_workflow_is_dispatch_only_and_restore_only_by_default():
    raw = WORKFLOW.read_text()
    assert "on:\n  workflow_dispatch:" in raw
    assert "workflow_run:" not in raw and "push:" not in raw and "schedule:" not in raw
    assert "default: restore-only" in raw
    assert "          - restore-only\n          - full" in raw
    assert "permissions:\n  contents: read" in raw


def test_workflow_reuses_hardened_ssh_and_uploads_only_evidence():
    text = WORKFLOW.read_text()
    for name in ("OVH_SSH_PRIVATE_KEY", "OVH_SSH_HOST", "OVH_SSH_KNOWN_HOSTS", "OVH_SSH_PORT", "OVH_SSH_USER"):
        assert name in text
    assert "~/.ssh/known_hosts" in text
    assert "actions/upload-artifact@v4" in text
    assert "path: recovery_evidence_production.json" in text
    assert "retention-days: 30" in text


def test_safe_mode_no_provider_secrets_and_no_production_restore_target():
    text = SCRIPT.read_text()
    assert "DRCLOUD_SAFE_MODE=true" in text
    assert '--network "$network"' in text and "docker network create --internal" in text
    for provider in ("QONTO_CREDENTIAL", "SUMUP_API_KEY", "PRESTASHOP_API_KEY", "SHOPCAISSE_API_KEY", "CRM_TOKEN"):
        assert provider not in text
    assert "src=$work/restored-data,dst=/data" in text
    assert "src=drcloud-data" not in text and "drcloud-data:/data" not in text


def test_fail_closed_locks_cleanup_and_failure_paths():
    text = SCRIPT.read_text()
    assert "/tmp/drcloud-os-recovery-gameday.lock" in text and "flock -n 8" in text
    assert "/tmp/drcloud-os-deploy.lock" in text and "flock -n 9" in text
    assert "GAME_DAY_BLOCKED_DEPLOYMENT_ACTIVE" in text
    assert "trap cleanup EXIT INT TERM" in text
    assert 'docker rm -f "$container"' in text and 'docker network rm "$network"' in text
    assert "PRODUCTION_BACKUP_INVALID" in text and "PRODUCTION_BACKUP_MISSING" in text
    assert text.count("RESTORE_FAILED") >= 2


def test_report_sanitization_and_unknown_n_minus_one_are_explicit():
    text = SCRIPT.read_text()
    for key in ("password", "secret", "token", "credential", "api_key", "private_key", "authorization"):
        assert key in text
    assert "ROLLBACK_NOT_PROVEN" in text
    assert "Never substitute HEAD^" in text or "Never substitute" in text
    assert 'history="$DRCLOUD_DEPLOYMENT_STATE_DIR/successful-commit-history"' in text


def test_full_environment_is_explicitly_isolated():
    text = SCRIPT.read_text()
    assert "OVH_EQUIVALENT_STAGING" in text
    assert "--read-only" in text and "--cap-drop ALL" in text
    assert "production_volume_mounted\":False" in text
