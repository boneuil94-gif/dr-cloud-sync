import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from dr_cloud_sync.inventory_web import InventoryApp
from dr_cloud_sync.modules import render_navigation
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
        {"id": "b", "name": "B", "status": "IN_PROGRESS", "steps": [
            {"name": "B1", "done": True}, {"name": "B2", "done": False},
        ]},
        {"id": "c", "name": "C", "status": "TODO"},
        {"id": "d", "name": "D", "status": "BLOCKED"},
    ]}
    assert service.module_progress(module) == 37.5
    assert 0 <= service.module_progress(module) <= 100


def test_global_and_remaining_are_calculated():
    data = RoadmapService(ROADMAP).load()
    expected = round(sum(m["weight"] * m["progress_percent"] / 100 for m in data["modules"]), 2)
    assert data["global_progress_percent"] == expected
    assert data["remaining_percent"] == round(100 - expected, 2)


def test_inconsistent_weights_are_rejected():
    data = raw_roadmap()
    data["modules"][0]["weight"] -= 1
    with pytest.raises(RoadmapError, match="100"):
        RoadmapService(ROADMAP).validate(data)


def test_invalid_status_is_rejected():
    data = raw_roadmap()
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
    assert 'href="/roadmap"' in render_navigation("roadmap")
    javascript = (ROOT / "src/dr_cloud_sync/static/roadmap.js").read_text(encoding="utf-8")
    assert "%" not in html
    for derived_value in ("49.3", "53.62", "46.38", "33.33", "46.15"):
        assert derived_value not in html and derived_value not in javascript
    assert request(app, "/roadmap")[0] == "200 OK"


def test_dashboard_loads_dynamic_roadmap_and_keeps_existing_routes(service):
    app = InventoryApp(service, roadmap_service=RoadmapService(ROADMAP))
    status, body = request(app, "/")
    assert status == "200 OK"
    assert b'class="dashboard-kpis"' in body
    assert b'id="systemStatusList"' in body
    assert b"29.7" not in body  # progress must only come from the API

    roadmap_status, roadmap_body = request(app, "/api/roadmap")
    payload = json.loads(roadmap_body)
    assert roadmap_status == "200 OK"
    assert len(payload["modules"]) == len(raw_roadmap()["modules"])
    assert payload["global_progress_percent"] == RoadmapService(ROADMAP).load()["global_progress_percent"]
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


def test_binary_substeps_and_blocked_credit_are_mathematically_explicit():
    service = RoadmapService(ROADMAP)
    fixture = {"milestones": [
        {"id": "done", "name": "Done", "status": "DONE"},
        {"id": "partial", "name": "Partial", "status": "IN_PROGRESS", "steps": [
            {"name": "one", "done": True}, {"name": "two", "done": True},
            {"name": "three", "done": False},
        ]},
        {"id": "todo", "name": "Todo", "status": "TODO"},
        {"id": "blocked", "name": "Blocked", "status": "BLOCKED"},
    ]}
    assert service.module_progress(fixture) == 41.67
    assert service.milestone_credit(fixture["milestones"][2]) == 0
    assert service.milestone_credit(fixture["milestones"][3]) == 0


def test_marketing_recognises_v1_without_claiming_production_future_done():
    marketing = next(m for m in RoadmapService(ROADMAP).load()["modules"] if m["id"] == "09-marketing")
    by_id = {item["id"]: item for item in marketing["milestones"]}
    assert by_id["09-marketing-m02"]["status"] == "DONE"  # Creative AI v1
    assert by_id["09-marketing-m04"]["status"] == "DONE"  # social pipeline v1
    assert by_id["09-marketing-m05"]["status"] == "BLOCKED"  # real providers
    assert by_id["09-marketing-m06"]["status"] == "BLOCKED"  # official compliance
    assert by_id["09-marketing-m07"]["status"] == "BLOCKED"  # real publishing
    assert by_id["09-marketing-m08"]["status"] == "TODO"  # live analytics
    assert by_id["09-marketing-m09"]["status"] == "DONE"  # sales-driven v1
    assert by_id["09-marketing-m10"]["status"] == "TODO"  # stock-driven
    assert by_id["09-marketing-m11"]["status"] == "TODO"  # purchase/margin
    assert by_id["09-marketing-m12"]["status"] == "TODO"  # measured learning loop
    assert by_id["09-marketing-m13"]["status"] == "DONE"  # provider-neutral analytics foundation
    assert marketing["progress_percent"] == 46.15


def test_sales_distinguishes_analytic_ledger_from_operational_sales():
    sales = next(m for m in RoadmapService(ROADMAP).load()["modules"] if m["id"] == "06-sales")
    by_id = {item["id"]: item for item in sales["milestones"]}
    assert all(by_id[f"06-sales-m0{number}"]["status"] == "DONE" for number in range(1, 7))
    assert "Sales Ledger analytique" in by_id["06-sales-m02"]["name"]
    assert "Import analytique manuel" in by_id["06-sales-m04"]["name"]
    assert by_id["06-sales-m07"]["status"] == "DONE"  # operational models
    assert by_id["06-sales-m08"]["status"] == "IN_PROGRESS"  # CSV, no verified network endpoint
    assert by_id["06-sales-m09"]["status"] == "DONE"  # GET-only paid orders
    assert "ShopCaisse" in sales["next"]
    assert sales["status"] == "IN_PROGRESS"
    assert sales["progress_percent"] == 84.62


def test_canonical_file_contains_no_derived_progress_values():
    data = raw_roadmap()
    assert "global_progress_percent" not in data
    assert all("progress_percent" not in module and "status" not in module for module in data["modules"])
