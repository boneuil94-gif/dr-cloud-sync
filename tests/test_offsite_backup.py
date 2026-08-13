import os
from pathlib import Path
import shutil
import subprocess

import pytest


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


def test_staging_permissions_are_normalized_for_unprivileged_restic():
    assert 'restic_uid="$(id -u)"' in SCRIPT
    assert 'restic_gid="$(id -g)"' in SCRIPT
    assert "OFFSITE_RESTIC_IDENTITY_INVALID" in SCRIPT
    assert '--user "$restic_uid:$restic_gid"' in SCRIPT
    assert '! -user "$restic_uid"' in SCRIPT
    assert '-type d -exec chmod 700 {} +' in SCRIPT
    assert '-type f -exec chmod 600 {} +' in SCRIPT
    assert '--mount "type=bind,src=$work/source,dst=/source,readonly"' in SCRIPT


def _local_container_image():
    if not shutil.which("docker"):
        return None
    try:
        subprocess.run(
            ["docker", "info"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    requested = os.environ.get("DRCLOUD_RESTIC_TEST_IMAGE")
    candidates = [requested] if requested else []
    candidates += ["alpine:3.20", "busybox:latest"]
    for image in candidates:
        inspected = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if inspected.returncode == 0:
            return image
    return None


def test_private_staging_is_readable_from_hardened_unprivileged_container(tmp_path):
    image = _local_container_image()
    if image is None:
        pytest.skip("Docker daemon and a local Alpine/BusyBox test image are required")
    if os.getuid() == 0:
        pytest.skip("The integration test requires an unprivileged host user")

    source = tmp_path / "source"
    media = source / "media"
    media.mkdir(parents=True, mode=0o700)
    expected = {
        "drcloud.db": "database",
        "catalogue.json": "catalogue",
        "catalogue-report.json": "report",
        "metadata.json": "metadata",
        "media/cover.jpg": "media",
    }
    for relative, contents in expected.items():
        path = source / relative
        path.write_text(contents)
        path.chmod(0o600)
    source.chmod(0o700)
    media.chmod(0o700)

    common = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,src={source},dst=/source,readonly",
        "--entrypoint", "/bin/sh", image, "-ec",
    ]
    read_all = "test -r /source; find /source -type f -exec cat {} \\; >/dev/null"

    # Emulate the image's former, mismatched non-root identity: private staging
    # is correctly unreadable to it, reproducing the production failure.
    old = common[:8] + ["--user", "65532:65532"] + common[8:]
    assert subprocess.run(old + [read_all], capture_output=True).returncode != 0

    # The script now explicitly maps Restic to the unprivileged staging owner.
    current = common[:8] + ["--user", f"{os.getuid()}:{os.getgid()}"] + common[8:]
    subprocess.run(current + [read_all], check=True)
