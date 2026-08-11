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
    assert text.count('"$PYTHON_BIN"') == 7


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
    final_run = text[text.index("docker run -d"):text.index('app_started_at=')]
    assert "type=bind,src=$work/restored-data,dst=/data" not in final_run
    assert "type=volume,src=$recovery_volume,dst=/data" in final_run
    assert "src=drcloud-data" not in text and "drcloud-data:/data" not in text


def test_temporary_recovery_volume_is_seeded_verified_and_cleaned_up():
    text = SCRIPT.read_text()
    assert 'recovery_volume="drcloud-recovery-data-${$}"' in text
    assert 'docker volume create "$recovery_volume"' in text
    assert 'docker volume rm -f "$recovery_volume"' in text
    assert "^drcloud-recovery-data-[0-9]+$" in text
    assert "RESTORE_VOLUME_PERMISSION_FAILED" in text

    seed = text[text.index("# Seed the validated"):text.index("# Fail closed unless ownership")]
    assert "--user 0:0 --network none --read-only" in seed
    assert "type=bind,src=$work/restored-data,dst=/seed,readonly" in seed
    assert "chown -R drcloud:drcloud /data" in seed
    assert "chmod 700 /data" in seed
    assert "chmod 600 /data/drcloud.db" in seed
    for provider in ("QONTO_CREDENTIAL", "SUMUP_API_KEY", "PRESTASHOP_API_KEY", "SHOPCAISSE_API_KEY", "CRM_TOKEN"):
        assert provider not in seed

    verification = text[text.index("# Fail closed unless ownership"):text.index("docker network create --internal")]
    assert "--network none --read-only" in verification
    assert 'stat -c %a /data)" = 700' in verification
    assert 'stat -c %a /data/drcloud.db)" = 600' in verification


def test_application_restore_uses_only_complete_official_bundle():
    text=SCRIPT.read_text()
    assert 'x.get("backup_class")=="APP_RESTORABLE"' in text
    assert 'x.get("runtime_files_complete") is True' in text
    for name in ("drcloud.db","catalogue.json","catalogue-report.json","metadata.json"):
        assert f'/data/backups/$backup_id/$runtime_file' in text
        assert f'cp /seed/drcloud.db /seed/catalogue.json /seed/catalogue-report.json /data/' in text
    restore=text[text.index('mkdir -m 700 "$work/bundle"'):text.index('restore_completed_at=')]
    assert '/data/catalogue.json' not in restore and '/data/catalogue-report.json' not in restore
    assert 'BACKUP_INCOMPLETE_RUNTIME_STATE' not in text


def test_runtime_json_and_inventory_readiness_fail_closed_before_boot():
    text=SCRIPT.read_text(); validation=text[text.index('if ! "$PYTHON_BIN" - "$selection"'):text.index('integrity_completed_at=')]
    assert 'json.load(open(os.path.join(restored,"catalogue.json")))' in validation
    assert 'report.get("ready_for_inventory") is not True' in validation
    assert 'not isinstance(rows,list) or not rows' in validation
    assert 'RESTORE_RUNTIME_STATE_INVALID' in validation


def test_final_recovery_container_keeps_image_user_and_hardening():
    text = SCRIPT.read_text()
    final_run = text[text.index("docker run -d"):text.index('app_started_at=')]
    assert "--user" not in final_run
    assert "--read-only" in final_run
    assert "--cap-drop ALL" in final_run
    assert "no-new-privileges" in final_run
    assert "type=volume,src=$recovery_volume,dst=/data" in final_run
    assert "type=bind" not in final_run
    assert "chmod 777" not in text


def test_application_data_directory_hardening_is_preserved():
    source = (ROOT / "src" / "dr_cloud_sync" / "inventory_web.py").read_text()
    assert "settings.data_dir.chmod(0o700)" in source


def test_recovery_uses_internal_docker_healthcheck_without_published_port():
    text = SCRIPT.read_text()
    docker_run = text[text.index("docker run -d"):text.index('app_started_at=')]
    assert " -p " not in docker_run
    assert "docker port" not in text
    assert "{{.State.Health.Status}}" in text
    assert 'healthy) health_ok_at=' in text
    assert "RESTORE_HEALTH_FAILED" in text
    assert "RESTORE_HEALTH_TIMEOUT" in text
    assert "RESTORE_APP_BOOT_FAILED" in text
    assert "{{.State.Running}}" in text
    assert 'docker exec "$container" python -c' in text
    assert "http://127.0.0.1:8080/api/roadmap" in text
    assert "exc.code in (401,403)" in text
    assert '"network_exposure":"NONE"' in text
    assert '"production_port_published":False' in text


def test_fail_closed_locks_cleanup_and_failure_paths():
    text = SCRIPT.read_text()
    assert "/tmp/drcloud-os-recovery-gameday.lock" in text and "flock -n 8" in text
    assert "/tmp/drcloud-os-deploy.lock" in text and "flock -n 9" in text
    assert "GAME_DAY_BLOCKED_DEPLOYMENT_ACTIVE" in text
    assert "trap cleanup EXIT INT TERM" in text
    assert 'docker rm -f "$container"' in text and 'docker network rm "$network"' in text
    assert 'docker volume rm -f "$recovery_volume"' in text
    assert "PRODUCTION_BACKUP_INVALID" in text and "PRODUCTION_BACKUP_MISSING" in text
    assert "RESTORE_APP_BOOT_FAILED" in text
    assert "RESTORE_HEALTH_FAILED" in text
    assert "RESTORE_HEALTH_TIMEOUT" in text


def test_report_sanitization_and_unknown_n_minus_one_are_explicit():
    text = SCRIPT.read_text()
    for key in ("password", "secret", "token", "credential", "api_key", "private_key", "authorization"):
        assert key in text
    assert "ROLLBACK_NOT_PROVEN" in text
    assert "Never substitute HEAD^" in text or "Never substitute" in text
    assert 'history="$DRCLOUD_DEPLOYMENT_STATE_DIR/successful-commit-history"' in text
    assert '"type":"TEMPORARY_DOCKER_VOLUME"' in text
    assert '"restored_database_copy":True' in text
    assert '"runtime_user":"drcloud"' in text


def test_full_environment_is_explicitly_isolated():
    text = SCRIPT.read_text()
    assert "OVH_EQUIVALENT_STAGING" in text
    assert "--read-only" in text and "--cap-drop ALL" in text
    assert "production_volume_mounted\":False" in text
