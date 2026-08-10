import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from dr_cloud_sync.inventory_web import InventoryApp
from dr_cloud_sync.roadmap import RoadmapError, RoadmapService
from test_inventory import request, service

ROOT = Path(__file__).parents[1]
ROADMAP = ROOT / "config" / "roadmap_v3.json"

def raw_roadmap(): return json.loads(ROADMAP.read_text(encoding="utf-8"))

def test_v3_scorecard_is_the_strict_source_of_truth():
    data = RoadmapService(ROADMAP).load()
    scores = {module["name"]: module["score"] for module in data["modules"]}
    assert data["version"] == 3
    assert data["global_score"] == data["global_progress_percent"] == 58
    assert data["remaining_percent"] == 42
    assert data["evidence_date"] == "2026-08-10"
    assert scores["Purchases"] == 63
    assert scores["Finance"] == 61
    assert scores["Stock"] == 67
    assert scores["Qonto"] == 78
    assert scores["Social"] == 22
    assert 75.93 not in scores.values() and 100 not in scores.values()

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
