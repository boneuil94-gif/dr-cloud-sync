import json
import logging
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from dr_cloud_sync.inventory_web import InventoryApp
import dr_cloud_sync.roadmap as roadmap_module
from dr_cloud_sync.roadmap import RoadmapError, RoadmapService
from test_inventory import request, service

ROOT = Path(__file__).parents[1]
ROADMAP = ROOT / "config" / "roadmap_v3.json"

def raw_roadmap(): return json.loads(ROADMAP.read_text(encoding="utf-8"))

def test_absent_environment_uses_v3_default(monkeypatch):
    monkeypatch.delenv("DRCLOUD_ROADMAP", raising=False)
    service = RoadmapService()
    assert service.configured_path is None
    assert service.path == ROADMAP
    assert service.load()["global_score"] == 58

def test_new_environment_path_is_used(monkeypatch):
    monkeypatch.setenv("DRCLOUD_ROADMAP", str(ROADMAP))
    service = RoadmapService()
    assert service.configured_path == service.path == ROADMAP
    assert service.diagnostic() == {
        "configured_path": str(ROADMAP), "effective_path": str(ROADMAP),
        "file_exists": True, "version": 3, "status": "OK",
    }

def test_deleted_legacy_path_falls_back_only_when_v3_exists(monkeypatch, caplog, tmp_path):
    replacement = tmp_path / "roadmap_v3.json"
    replacement.write_text(ROADMAP.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(roadmap_module, "DEFAULT_ROADMAP", replacement)
    with caplog.at_level(logging.WARNING, logger="drcloud.roadmap"):
        service = RoadmapService("/app/docs/drcloud-os-roadmap.json")
    assert service.path == replacement
    assert "ROADMAP_LEGACY_PATH" in caplog.text
    assert service.load()["global_score"] == 58

    replacement.unlink()
    service = RoadmapService("/app/docs/drcloud-os-roadmap.json")
    assert service.path == RoadmapService.LEGACY_PATH
    with pytest.raises(RoadmapError, match="Roadmap illisible"):
        service.load()

def test_missing_and_invalid_files_are_diagnosed_without_fallback(tmp_path):
    missing = RoadmapService(tmp_path / "custom.json")
    assert missing.diagnostic()["status"] == "MISSING"
    with pytest.raises(RoadmapError, match="Roadmap illisible"):
        missing.load()
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{not-json", encoding="utf-8")
    invalid = RoadmapService(invalid_path)
    assert invalid.diagnostic()["status"] == "INVALID"
    with pytest.raises(RoadmapError, match="Roadmap illisible"):
        invalid.load()

def test_persistent_production_environment_is_migrated(tmp_path):
    env_file = tmp_path / "drcloud.env"
    env_file.write_text("DRCLOUD_SAFE_MODE=true\nDRCLOUD_ROADMAP=/app/docs/drcloud-os-roadmap.json\n", encoding="utf-8")
    script = ROOT / "deploy/ovh/configure-roadmap-env.sh"
    result = subprocess.run(
        [script], env={**os.environ, "DRCLOUD_ENV_FILE": str(env_file)},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "DRCLOUD_ROADMAP=/app/config/roadmap_v3.json" in env_file.read_text(encoding="utf-8")
    assert "/app/docs/drcloud-os-roadmap.json" not in env_file.read_text(encoding="utf-8")
    assert env_file.stat().st_mode & 0o777 == 0o600

def test_absent_roadmap_setting_is_added_to_persistent_environment(tmp_path):
    env_file = tmp_path / "drcloud.env"
    env_file.write_text("DRCLOUD_SAFE_MODE=true\n", encoding="utf-8")
    result = subprocess.run(
        [ROOT / "deploy/ovh/configure-roadmap-env.sh"],
        env={**os.environ, "DRCLOUD_ENV_FILE": str(env_file)}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert env_file.read_text(encoding="utf-8").endswith("DRCLOUD_ROADMAP=/app/config/roadmap_v3.json\n")

def test_v3_scorecard_is_the_strict_source_of_truth():
    data = RoadmapService(ROADMAP).load()
    scores = {module["name"]: module["score"] for module in data["modules"]}
    assert data["version"] == 3
    assert data["global_score"] == data["global_progress_percent"] == 58
    assert data["remaining_percent"] == 42
    assert data["evidence_date"] == "2026-08-12"
    assert scores["Purchases"] == 63
    assert scores["Finance"] == 61
    assert scores["Stock"] == 67
    assert scores["Qonto"] == 78
    assert scores["Social"] == 22
    assert 75.93 not in scores.values() and 100 not in scores.values()

def test_production_recovery_proof_closes_backup_p0_without_inventing_scores():
    data = RoadmapService(ROADMAP).load()
    proof = data["production_recovery_gameday_2026_08_12"]
    assert proof["result"] == "PRODUCTION_DATA_PROVEN"
    assert proof["app_boot"] == "APP_BOOT_OK"
    assert proof["health"] == "HEALTH_OK"
    assert proof["priorities"]["p0_backup_restorable"] == "CLOSED_PROVEN"
    assert proof["priorities"]["p1_rollback_recovery"] == "PARTIAL_RESTORE_PROVEN_ROLLBACK_OPEN"
    assert proof["scores"]["global_strict"] == {"before": 58, "after": 58}
    assert proof["scores"]["production_maturity"] == {"before": 49, "after": 49}
    assert proof["scores"]["deployment"] == {"before": 68, "after": 68}
    assert proof["rollback"] == "NOT_REQUESTED"
    assert proof["schema_compatibility"] == "UNKNOWN"
    assert proof["backup_location"] == "BACKUP_ON_HOST_ONLY"

def test_dimensions_are_audited_facts_not_a_module_average():
    data = RoadmapService(ROADMAP).load()
    assert data["dimensions"] == {"code_maturity":72,"production_maturity":49,"business_completeness":55,"security":74,"observability":61,"test_quality":76,"ux":60}
    assert sum(module["score"] for module in data["modules"]) / len(data["modules"]) != data["global_score"]

def test_every_module_has_auditable_card_fields_and_no_false_done():
    data = RoadmapService(ROADMAP).load()
    required = {"score", "status", "justification", "blocker", "next_step", "evidence_level"}
    assert all(required <= module.keys() for module in data["modules"])
    assert all(module["status"] != "DONE" for module in data["modules"])
    assert all(module["status"] != "DONE_PROVEN" or module["evidence_level"] == "PRODUCTION_PROVEN" for module in data["modules"])

def test_api_and_ui_use_the_same_structured_source(service):
    app = InventoryApp(service, roadmap_service=RoadmapService(ROADMAP))
    status, body = request(app, "/api/roadmap")
    assert status == "200 OK"
    assert json.loads(body) == RoadmapService(ROADMAP).load()
    javascript = (ROOT / "src/dr_cloud_sync/static/roadmap.js").read_text(encoding="utf-8")
    html = (ROOT / "src/dr_cloud_sync/static/roadmap.html").read_text(encoding="utf-8")
    assert 'fetch("/api/roadmap")' in javascript
    assert "Pourquoi le score n’est pas 75.93 %" in html
    assert request(app, "/roadmap")[0] == "200 OK"
    health_status, health_body = request(app, "/api/roadmap/health")
    assert health_status == "200 OK"
    assert json.loads(health_body)["status"] == "OK"

def test_legacy_read_routes_and_dashboard_alias_remain_compatible(service):
    app = InventoryApp(service, roadmap_service=RoadmapService(ROADMAP))
    for path in ("/roadmap", "/catalogue", "/inventaire", "/api/dashboard", "/api/state"):
        assert request(app, path)[0] == "200 OK"
    assert json.loads(request(app, "/api/dashboard")[1])["progress_percent"] == 58

def test_invalid_score_status_and_evidence_are_rejected():
    roadmap_service = RoadmapService(ROADMAP)
    for field, value, message in (("score",101,"Score"),("status","DONE","Statut"),("evidence_level","UNKNOWN","preuve")):
        data = deepcopy(raw_roadmap()); data["modules"][0][field] = value
        with pytest.raises(RoadmapError, match=message): roadmap_service.validate(data)

def test_frontend_assets_contain_no_obsolete_card_values():
    assets = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in ("src/dr_cloud_sync/static/roadmap.html","src/dr_cloud_sync/static/roadmap.js"))
    for old_value in ("Purchases 100","Finance 81.82","Stock 90","Catalogue 90"):
        assert old_value not in assets
    assert "75.93" not in (ROOT / "src/dr_cloud_sync/static/roadmap.js").read_text()
    assert "75.93" not in ROADMAP.read_text()

def test_dashboard_model_tolerates_missing_data_and_clamps_progress():
    script = """const {dashboardModel,clampPercent}=require('./src/dr_cloud_sync/static/dashboard.js');const empty=dashboardModel({},{});if(empty.modules.length!==0||empty.progress!==0||empty.remaining!==100)process.exit(1);const partial=dashboardModel({global_progress_percent:180,modules:[{name:'A'}]},{});if(partial.progress!==100||partial.modules.length!==1||clampPercent(-4)!==0)process.exit(2);"""
    result = subprocess.run(["node","-e",script],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
