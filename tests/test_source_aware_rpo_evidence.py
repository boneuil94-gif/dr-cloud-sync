import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "docs/evidence/source_aware_rpo_evidence_production_2026-08-19.json"


def test_source_aware_rpo_proof_is_real_bounded_and_not_overcredited():
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert data["evidence_level"] == "OFFSITE_ENCRYPTED_BACKUP_RESTORE_SOURCE_AWARE_RPO"
    assert data["provenance"]["backup_run_id"] == 32263186935
    assert data["provenance"]["recovery_run_id"] == 32263769005
    assert data["backup"]["result"] == "OFFSITE_REMOTE_CHECK_PROVEN"
    assert data["backup"]["remote_check"] == "PROVEN"
    assert data["recovery"]["result"] == "OFFSITE_RESTORE_PROVEN"
    assert data["recovery"]["app_boot"] == "APP_BOOT_OK"
    assert data["recovery"]["health"] == "HEALTH_OK"

    rpo = data["recovery"]["rpo"]
    assert rpo["method"] == "live_vs_backup_source_watermarks"
    assert rpo["live_watermark_available"] is True
    assert rpo["backup_watermark_available"] is True
    assert rpo["observed_rpo_seconds"] == 0
    assert rpo["business_data_gap_seconds"] == 0
    assert rpo["comparable_sources"] == 1
    assert rpo["unmeasurable_sources"] == 12
    assert rpo["confidence"] == "MEDIUM"
    assert rpo["confidence"] != "HIGH"

    safety = data["safety"]
    assert safety["local_backup_used_for_restore"] is False
    assert safety["production_volume_mounted"] is False
    assert safety["production_port_published"] is False
    assert safety["live_watermark_used_for_measurement_only"] is True
    assert safety["cloud_material_persisted"] is False
    assert data["assessment"]["priority_status"] == "PARTIAL_PRODUCTION_PROVEN"
    assert data["assessment"]["score_credit"] == "NONE_WITHOUT_REPRODUCIBLE_RESCORE"


def test_persisted_evidence_contains_no_secret_material_contract_keys():
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    serialized = json.dumps(data).lower()
    forbidden = ("password", "credential", "private_key", "api_key", "authorization", "bearer ")
    assert all(token not in serialized for token in forbidden)
