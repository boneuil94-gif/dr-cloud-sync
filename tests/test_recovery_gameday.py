import json
import os
from pathlib import Path
import re
import subprocess

import pytest


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
    assert 'history="$DRCLOUD_DEPLOYMENT_STATE_DIR/successful-commit-history"' in (text + (ROOT / 'deploy/ovh/recovery-rollback.sh').read_text())
    assert '"type":"TEMPORARY_DOCKER_VOLUME"' in text
    assert '"restored_database_copy":True' in text
    assert '"runtime_user":"drcloud"' in text


def test_full_environment_is_explicitly_isolated():
    text = SCRIPT.read_text()
    assert "OVH_EQUIVALENT_STAGING" in text
    assert "--read-only" in text and "--cap-drop ALL" in text
    assert "production_volume_mounted\":False" in text


def _report_generator_source():
    """Return the Python heredoc that builds and sanitizes recovery evidence."""
    text = SCRIPT.read_text()
    marker = 'report="$DRCLOUD_DEPLOYMENT_STATE_DIR/recovery_evidence_production.json"'
    block = text[text.index(marker):]
    return block.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _run_report_generator(tmp_path, source=None):
    source = source or _report_generator_source()
    selection = tmp_path / "selection.json"
    timings = tmp_path / "timings.json"
    phases = tmp_path / "phases.json"
    report = tmp_path / "recovery_evidence_production.json"
    selection.write_text('{"backup_id":"drcloud-os-backup-test","created_at":"2026-08-12T00:00:00Z"}')
    timings.write_text('{"integrity_check":"ok"}')
    phases.write_text("""{
      "started_at":"2026-08-12T00:00:00Z",
      "backup_selected_at":"2026-08-12T00:00:01Z",
      "restore_completed_at":"2026-08-12T00:00:02Z",
      "integrity_completed_at":"2026-08-12T00:00:03Z",
      "app_started_at":"2026-08-12T00:00:04Z",
      "health_ok_at":"2026-08-12T00:00:05Z",
      "business_validation_completed_at":"2026-08-12T00:00:06Z"
    }""")
    result = subprocess.run(
        ["python", "-", selection, timings, phases, report, "restore-only", "NOT_REQUESTED", "", ""],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, report


def test_normal_recovery_report_passes_sanitizer_and_is_written(tmp_path):
    result, path = _run_report_generator(tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(path.read_text())
    assert report["restore"]["result"] == "PRODUCTION_DATA_PROVEN"
    assert 'echo "PRODUCTION_DATA_PROVEN"' in SCRIPT.read_text()


def test_external_provider_auth_none_is_accepted(tmp_path):
    result, path = _run_report_generator(tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(path.read_text())["safety"]["external_provider_auth"] == "NONE"


def test_all_generated_recovery_evidence_keys_pass_the_forbidden_key_audit(tmp_path):
    result, path = _run_report_generator(tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(path.read_text())
    forbidden = ("password", "secret", "token", "credential", "api_key", "private_key", "authorization")

    def audit(value):
        if isinstance(value, dict):
            assert all(not any(word in key.lower() for word in forbidden) for key in value)
            for child in value.values():
                audit(child)
        elif isinstance(value, list):
            for child in value:
                audit(child)

    audit(report)


@pytest.mark.parametrize("forbidden_key", ["password", "secret", "token", "credential", "api_key"])
def test_recovery_report_rejects_forbidden_evidence_keys(tmp_path, forbidden_key):
    source = _report_generator_source().replace(
        "scan(report)", f'report["{forbidden_key}"]="NONE"; scan(report)'
    )
    result, path = _run_report_generator(tmp_path, source)
    assert result.returncode != 0
    assert "forbidden evidence key" in result.stderr
    assert not path.exists()


@pytest.mark.parametrize("unsafe_value", ["Bearer abc123", "-----BEGIN PRIVATE KEY-----"])
def test_recovery_report_rejects_sensitive_value_patterns(tmp_path, unsafe_value):
    source = _report_generator_source().replace(
        "scan(report)", f'report["message"]={unsafe_value!r}; scan(report)'
    )
    result, path = _run_report_generator(tmp_path, source)
    assert result.returncode != 0
    assert "credential pattern" in result.stderr
    assert not path.exists()

ROLLBACK_SCRIPT = ROOT / "deploy/ovh/recovery-rollback.sh"
DEPLOYMENT_STATE = ROOT / "deploy/ovh/deployment-state.sh"


def test_deployment_state_records_ordered_deduplicated_known_good_history(tmp_path):
    state = tmp_path / ".deployment-state"
    state.mkdir(mode=0o755)
    user = subprocess.run(["id", "-un"], text=True, capture_output=True, check=True).stdout.strip()
    group = subprocess.run(["id", "-gn"], text=True, capture_output=True, check=True).stdout.strip()
    env = {**os.environ, "DRCLOUD_DEPLOY_USER": user, "DRCLOUD_DEPLOY_GROUP": group}
    commits = ["a" * 40, "b" * 40, "b" * 40, "c" * 40]
    for commit in commits:
        subprocess.run([DEPLOYMENT_STATE, state, commit], env=env, check=True)
    history = state / "successful-commit-history"
    assert history.read_text().splitlines() == ["a" * 40, "b" * 40, "c" * 40]
    assert (state / "last-successful-commit").read_text().strip() == "c" * 40
    assert history.stat().st_mode & 0o777 == 0o444
    publisher = DEPLOYMENT_STATE.read_text()
    assert "mktemp" in publisher and 'mv -f -- "$history_tmp"' in publisher
    assert "HEAD^" not in publisher


def test_full_rollback_is_real_isolated_n_n1_n_and_fail_closed():
    main = SCRIPT.read_text()
    rollback = ROLLBACK_SCRIPT.read_text()
    assert 'rollback_reason="ROLLBACK_HISTORY_INSUFFICIENT"' in rollback
    assert "refs/remotes/origin/main" in rollback and "merge-base --is-ancestor" in rollback
    assert 'git -C "$repo" worktree add --detach' in rollback
    assert 'n1_image="drcloud-recovery-n1-${$}"' in main
    assert '--tag "$n1_image"' in rollback and '--tag drcloud-os:local' not in rollback
    assert 'rollback_volume="drcloud-rollback-data-${$}"' in main
    assert "drcloud-data" not in rollback
    phases = [rollback.index('rollback_health "$n_container" N'), rollback.index('rollback_health "$n1_container" N_MINUS_1'), rollback.index('rollback_health "$n_container" N_RETURN')]
    assert phases == sorted(phases)
    assert "ROLLBACK_SCHEMA_INCOMPATIBLE" in rollback
    assert "ROLLBACK_DATA_LOSS_DETECTED" in rollback
    assert 'rollback_result="ROLLBACK_PROVEN"' in rollback
    assert 'rollback_result" != "ROLLBACK_PROVEN"' in main


def _rollback_facts_function_source():
    text = ROLLBACK_SCRIPT.read_text()
    start = text.index("rollback_facts() {")
    return text[start:text.index("\n}\n\n[[", start) + 2]


def _capture_rollback_facts(tmp_path, payload):
    docker = tmp_path / "docker"
    docker.write_text("""#!/bin/bash
set -eu
[[ "$1" == exec ]]
shift
if [[ "${1:-}" != -i ]]; then
  cat >/dev/null
  exit 0
fi
shift 2
cat >/dev/null
cat "$ROLLBACK_FACT_FIXTURE"
""")
    docker.chmod(0o755)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(payload)
    destination = tmp_path / "facts.json"
    command = _rollback_facts_function_source() + '\nrollback_facts container "$1"\n'
    result = subprocess.run(
        ["bash", "-c", command, "rollback-facts-test", destination],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "PYTHON_BIN": subprocess.check_output(["which", "python"], text=True).strip(),
             "ROLLBACK_FACT_FIXTURE": str(fixture)},
        text=True, capture_output=True, check=False,
    )
    return result, destination


def _valid_rollback_snapshot(count=3, fingerprint="stable-pks"):
    return {
        "quick_check": "ok", "integrity_check": "ok", "foreign_key_check": "OK",
        "schema_fingerprint": "stable-schema", "table_counts": {"sales": count},
        "primary_key_fingerprint": {"sales": fingerprint},
    }


def test_rollback_fact_capture_attaches_docker_stdin_and_publishes_valid_json(tmp_path):
    result, destination = _capture_rollback_facts(tmp_path, json.dumps(_valid_rollback_snapshot()))
    assert result.returncode == 0, result.stderr
    assert json.loads(destination.read_text()) == _valid_rollback_snapshot()
    assert 'docker exec -i "$1" python -' in ROLLBACK_SCRIPT.read_text()


@pytest.mark.parametrize("payload", ["", "not-json"])
def test_rollback_fact_capture_rejects_empty_or_invalid_json(tmp_path, payload):
    result, destination = _capture_rollback_facts(tmp_path, payload)
    assert result.returncode != 0
    assert not destination.exists()
    assert not list(tmp_path.glob("facts.json.tmp.*"))


def _run_rollback_comparison(tmp_path, snapshots):
    text = ROLLBACK_SCRIPT.read_text()
    marker = '"$PYTHON_BIN" - "$before" "$middle" "$returned" <<\'PY\''
    source = text[text.index(marker):].split("\n", 1)[1].split("\nPY\n", 1)[0]
    paths = []
    for index, snapshot in enumerate(snapshots):
        path = tmp_path / f"snapshot-{index}.json"
        path.write_text(json.dumps(snapshot))
        paths.append(path)
    return subprocess.run(["python", "-", *paths], input=source, text=True,
                          capture_output=True, check=False)


def test_valid_identical_rollback_snapshots_pass(tmp_path):
    snapshot = _valid_rollback_snapshot()
    result = _run_rollback_comparison(tmp_path, [snapshot, snapshot, snapshot])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("changed", [
    _valid_rollback_snapshot(count=2),
    _valid_rollback_snapshot(fingerprint="changed-pks"),
])
def test_real_row_loss_or_primary_key_change_is_detected(tmp_path, changed):
    baseline = _valid_rollback_snapshot()
    result = _run_rollback_comparison(tmp_path, [baseline, changed, baseline])
    assert result.returncode == 1


def test_invalid_snapshot_is_capture_failure_not_data_loss_classification():
    rollback = ROLLBACK_SCRIPT.read_text()
    assert 'comparison_status == 2' in rollback
    assert 'rollback_reason="ROLLBACK_FACT_CAPTURE_FAILED"' in rollback
    assert 'comparison_status == 2 )); then\n    data_loss_check="NOT_EXECUTED"' in rollback


def test_full_cleanup_and_failure_evidence_upload_contract():
    main = SCRIPT.read_text(); workflow = WORKFLOW.read_text()
    for value in ('"$n_container" "$n1_container"', '"$rollback_network"', '"$rollback_volume"', '"$n1_image"', 'worktree remove --force', 'rm -rf -- "$work"'):
        assert value in main
    assert "continue-on-error: true" in workflow
    assert workflow.count("if: always()") >= 2
    assert "steps.recovery.outcome" in workflow
    assert 'ROLLBACK_PROVEN' in workflow
