from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = (ROOT / "deploy/ovh/offsite-recovery-gameday.sh").read_text()
WORKFLOW = (ROOT / ".github/workflows/drcloud-os-offsite-recovery-gameday.yml").read_text()


def _commands(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_restore_is_remote_only_and_destination_must_be_empty():
    commands = _commands(SCRIPT)
    assert '[[ -z "$(find "$restore" -mindepth 1 -print -quit)" ]]' in SCRIPT
    assert "restic restore" in SCRIPT
    assert "cp /data/backups/" not in commands
    assert "rsync /data/backups/" not in commands
    assert "src=production-data" not in commands
    assert "docker compose cp" not in commands


def test_integrity_runtime_and_sqlite_checks_are_mandatory():
    for check in ("required_runtime_files", "sha256", "json.load", "quick_check", "integrity_check", "foreign_key_check"):
        assert check in SCRIPT
    assert "OFFSITE_RESTORE_RUNTIME_INVALID" in SCRIPT


def test_isolated_safe_application_boot_and_health_probe():
    for hardening in ("--network none", "--internal", "--read-only", "--cap-drop ALL", "no-new-privileges", "DRCLOUD_SAFE_MODE=true"):
        assert hardening in SCRIPT
    assert "-p " not in _commands(SCRIPT)
    assert "/api/roadmap" in SCRIPT and "OFFSITE_HEALTH_FAILED" in SCRIPT


def test_sanitized_evidence_contract_and_low_confidence_proxy():
    for value in ("OFFSITE_ENCRYPTED_BACKUP_RESTORE", "OFFSITE_RESTORE_PROVEN", "APP_BOOT_OK", "HEALTH_OK", "RESTIC_CLIENT_SIDE_ENCRYPTED", "OFF_HOST_OBJECT_STORAGE"):
        assert value in SCRIPT
    assert '"rpo_confidence":comparison.get("confidence") if backup_watermark else "LOW"' in SCRIPT
    assert '"local_backup_used_for_restore":False' in SCRIPT
    assert '"cloud_material_persisted":False' in SCRIPT
    assert 'forbidden=("password","secret","token","credential"' in SCRIPT


def test_gameday_material_cleanup_and_red_workflow():
    assert "environment: production" in WORKFLOW
    assert "trap cleanup EXIT INT TERM" in WORKFLOW and "shred -u" in WORKFLOW
    assert 'test ! -e "$runtime"' in WORKFLOW
    assert "path: offsite_recovery_evidence_production.json" in WORKFLOW
    assert 'test "$STEP_OUTCOME" = success' in WORKFLOW


def test_all_recovery_phase_failures_are_terminal():
    for result in (
        "OFFSITE_NOT_CONFIGURED",
        "RESTIC_IMAGE_NOT_IMMUTABLE",
        "OFFSITE_RESTORE_DESTINATION_NOT_EMPTY",
        "OFFSITE_SNAPSHOT_ABSENT",
        "OFFSITE_REMOTE_CHECK_FAILED",
        "OFFSITE_RESTORE_FAILED",
        "OFFSITE_RESTORE_RUNTIME_INVALID",
        "OFFSITE_APP_BOOT_FAILED",
        "OFFSITE_HEALTH_FAILED",
        "OFFSITE_BUSINESS_PROBE_FAILED",
    ):
        assert result in SCRIPT
    assert "set -Eeuo pipefail" in SCRIPT


def test_restore_staging_is_private_and_writable_by_unprivileged_restic():
    assert 'restic_uid="$(id -u)"; restic_gid="$(id -g)"' in SCRIPT
    assert 'chmod 700 "$restore"' in SCRIPT
    assert '--user "$restic_uid:$restic_gid"' in SCRIPT
    assert '--mount "type=bind,src=$restore,dst=/restore"' in SCRIPT
    assert "OFFSITE_RESTORE_STAGING_INVALID" in SCRIPT
    assert "OFFSITE_RESTIC_IDENTITY_INVALID" in SCRIPT
