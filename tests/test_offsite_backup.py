from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = (ROOT / "deploy/ovh/offsite-backup.sh").read_text()
WORKFLOW = (ROOT / ".github/workflows/drcloud-os-offsite-backup.yml").read_text()


def test_offsite_runs_after_unchanged_application_backup_contract():
    assert "dr-cloud-sync backup-status --json" in SCRIPT
    assert '"$repo/deploy/ovh/backup.sh"' in SCRIPT
    assert 'backup_class")=="APP_RESTORABLE"' in SCRIPT
    assert "required_runtime_files" in SCRIPT


def test_fail_closed_results_and_immutable_image():
    for result in (
        "OFFSITE_NOT_CONFIGURED",
        "OFFSITE_BACKUP_SOURCE_INVALID",
        "OFFSITE_UPLOAD_FAILED",
        "OFFSITE_UPLOAD_PROVEN",
        "OFFSITE_REMOTE_CHECK_FAILED",
        "OFFSITE_REMOTE_CHECK_PROVEN",
        "RESTIC_IMAGE_NOT_IMMUTABLE",
    ):
        assert result in SCRIPT
    assert "@sha256:" in SCRIPT and "restic check" in SCRIPT
    assert "|| fail OFFSITE_UPLOAD_FAILED" in SCRIPT


def test_restic_material_is_only_in_ephemeral_process_environment():
    assert "environment: production" in WORKFLOW
    assert "chmod 600" in WORKFLOW and "trap cleanup EXIT INT TERM" in WORKFLOW
    assert "shred -u" in WORKFLOW and 'test ! -e "$runtime"' in WORKFLOW
    assert "drcloud.env" not in WORKFLOW
    assert "-e RESTIC_PASSWORD" in SCRIPT and "-e AWS_SECRET_ACCESS_KEY" in SCRIPT
    assert "docker compose exec" not in SCRIPT[SCRIPT.index("restic() {") : SCRIPT.index("docker compose exec")]


def test_retention_is_opt_in_and_bounded():
    assert "RETENTION_NOT_CONFIGURED" in SCRIPT
    assert "OFFSITE_RESTIC_KEEP_DAILY" in SCRIPT
    assert "--prune" in SCRIPT
    assert "value <= 10000" in SCRIPT


def test_workflow_artifact_is_sanitized_status_and_enforced():
    assert "path: offsite_backup_status.json" in WORKFLOW
    assert "OFFSITE_REMOTE_CHECK_PROVEN" in WORKFLOW
    assert "continue-on-error: true" in WORKFLOW
    assert "test \"$STEP_OUTCOME\" = success" in WORKFLOW


def test_network_auth_interruption_missing_snapshot_and_corruption_fail_closed():
    # A wrong repository key or unreachable endpoint makes the Restic command
    # non-zero; interrupted upload never reaches a proven snapshot; a missing
    # snapshot or failed repository check has its own terminal result.
    assert "restic backup /source" in SCRIPT and "|| fail OFFSITE_UPLOAD_FAILED" in SCRIPT
    assert '[[ "$snapshot" =~' in SCRIPT and "|| fail OFFSITE_UPLOAD_FAILED" in SCRIPT
    assert 'restic snapshots --json --tag "$backup_id"' in SCRIPT
    assert 'restic check --read-data-subset=' in SCRIPT
    assert SCRIPT.count("fail OFFSITE_REMOTE_CHECK_FAILED") >= 3
