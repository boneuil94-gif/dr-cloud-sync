import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from dr_cloud_sync.inventory_web import InventoryApp
from dr_cloud_sync.roadmap import RoadmapError, RoadmapService
from test_inventory import request, service

ROOT = Path(__file__).parents[1]
ROADMAP = ROOT / "docs" / "drcloud-os-roadmap.json"


def raw_roadmap():
    return json.loads(ROADMAP.read_text(encoding="utf-8"))


def test_roadmap_json_is_readable_and_weights_total_100():
    data = RoadmapService(ROADMAP).load()
    assert sum(module["weight"] for module in data["modules"]) == 100
    assert len(data["modules"]) == 13


def test_module_progress_is_calculated_from_milestones():
    service = RoadmapService(ROADMAP)
    module = {"milestones": [
        {"id": "a", "name": "A", "status": "DONE"},
        {"id": "b", "name": "B", "status": "IN_PROGRESS"},
        {"id": "c", "name": "C", "status": "TODO"},
        {"id": "d", "name": "D", "status": "BLOCKED"},
    ]}
    assert service.module_progress(module) == 37.5
    assert 0 <= service.module_progress(module) <= 100


def test_global_and_remaining_are_calculated():
    data = RoadmapService(ROADMAP).load()
    expected = round(sum(m["weight"] * m["progress_percent"] / 100 for m in data["modules"]), 2)
    assert data["global_progress_percent"] == expected == 29.89
    assert data["remaining_percent"] == 100 - expected == 70.11


def test_inconsistent_weights_are_rejected():
    data = raw_roadmap()
    data["modules"][0]["weight"] -= 1
    with pytest.raises(RoadmapError, match="100"):
        RoadmapService(ROADMAP).validate(data)


@pytest.mark.parametrize("target", ["module", "milestone"])
def test_invalid_status_is_rejected(target):
    data = raw_roadmap()
    if target == "module":
        data["modules"][0]["status"] = "UNKNOWN"
    else:
        data["modules"][0]["milestones"][0]["status"] = "UNKNOWN"
    with pytest.raises(RoadmapError, match="Statut"):
        RoadmapService(ROADMAP).validate(data)


def test_invalid_milestone_is_rejected():
    data = raw_roadmap()
    data["modules"][0]["milestones"] = [{"id": "missing-fields"}]
    with pytest.raises(RoadmapError, match="Jalon"):
        RoadmapService(ROADMAP).validate(data)


def test_roadmap_page_uses_service_and_has_no_hardcoded_percentage(service):
    class StubRoadmapService:
        def __init__(self): self.called = 0
        def load(self):
            self.called += 1
            return {"global_progress_percent": 12, "remaining_percent": 88, "modules": []}

    stub = StubRoadmapService()
    app = InventoryApp(service, roadmap_service=stub)
    status, body = request(app, "/api/roadmap")
    assert status == "200 OK" and json.loads(body)["global_progress_percent"] == 12
    assert stub.called == 1
    html = (ROOT / "src/dr_cloud_sync/static/roadmap.html").read_text(encoding="utf-8")
    assert 'href="/roadmap"' in html
    assert "%" not in html
    assert request(app, "/roadmap")[0] == "200 OK"


def test_dashboard_loads_dynamic_roadmap_and_keeps_existing_routes(service):
    app = InventoryApp(service, roadmap_service=RoadmapService(ROADMAP))
    status, body = request(app, "/")
    assert status == "200 OK"
    assert b'id="progressHero"' in body
    assert b'id="moduleGrid"' in body
    assert b"29.7" not in body  # progress must only come from the API

    roadmap_status, roadmap_body = request(app, "/api/roadmap")
    payload = json.loads(roadmap_body)
    assert roadmap_status == "200 OK"
    assert len(payload["modules"]) == len(raw_roadmap()["modules"])
    assert payload["global_progress_percent"] == 29.89
    for path in ("/roadmap", "/catalogue", "/inventaire", "/api/dashboard", "/api/state"):
        assert request(app, path)[0] == "200 OK"


def test_dashboard_model_tolerates_missing_data_and_clamps_progress():
    script = r"""
const {dashboardModel, clampPercent} = require('./src/dr_cloud_sync/static/dashboard.js');
const empty = dashboardModel({}, {});
if (empty.modules.length !== 0 || empty.progress !== 0 || empty.remaining !== 100) process.exit(1);
const partial = dashboardModel({global_progress_percent: 180, modules: [{name: 'A'}]}, {});
if (partial.progress !== 100 || partial.modules.length !== 1 || clampPercent(-4) !== 0) process.exit(2);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
