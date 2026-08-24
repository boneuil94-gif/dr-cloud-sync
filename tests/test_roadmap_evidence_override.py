import json

import pytest

from dr_cloud_sync.roadmap import RoadmapError, RoadmapService


def _base_roadmap(tmp_path):
    path = tmp_path / "roadmap.json"
    path.write_text(json.dumps({
        "version": 3,
        "global_score": 58,
        "dimensions": {"deployment": 85},
        "evidence_date": "2026-08-20",
        "modules": [{
            "id": "deployment", "name": "Deployment", "score": 85,
            "status": "PARTIAL", "justification": "old", "blocker": "old blocker",
            "next_step": "old next", "evidence_level": "PRODUCTION_PROVEN",
        }],
        "priorities": ["old"],
        "blockers": ["old"],
    }), encoding="utf-8")
    return path


def test_bounded_override_updates_truth_without_changing_scores(tmp_path):
    roadmap = _base_roadmap(tmp_path)
    override = tmp_path / "override.json"
    override.write_text(json.dumps({
        "version": 1,
        "evidence_date": "2026-08-24",
        "module_updates": {"deployment": {
            "justification": "retention proven",
            "blocker": "3 RPO sources remain",
            "next_step": "measure them",
            "evidence_level": "PRODUCTION_PROVEN",
        }},
        "priorities": ["measure RPO"],
        "blockers": ["provider authority open"],
        "evidence": {"retention": {"status": "PRODUCTION_PROVEN"}},
    }), encoding="utf-8")

    result = RoadmapService(roadmap, evidence_override=override).load()

    assert result["global_score"] == 58
    assert result["dimensions"] == {"deployment": 85}
    assert result["modules"][0]["score"] == 85
    assert result["modules"][0]["justification"] == "retention proven"
    assert result["evidence_date"] == "2026-08-24"
    assert result["evidence_update"]["retention"]["status"] == "PRODUCTION_PROVEN"


def test_override_rejects_score_or_unknown_module_fields(tmp_path):
    roadmap = _base_roadmap(tmp_path)
    override = tmp_path / "override.json"
    override.write_text(json.dumps({
        "version": 1,
        "module_updates": {"deployment": {"score": 99}},
    }), encoding="utf-8")

    with pytest.raises(RoadmapError, match="champs interdits"):
        RoadmapService(roadmap, evidence_override=override).load()


def test_override_rejects_top_level_score_changes(tmp_path):
    roadmap = _base_roadmap(tmp_path)
    override = tmp_path / "override.json"
    override.write_text(json.dumps({"version": 1, "global_score": 99}), encoding="utf-8")

    with pytest.raises(RoadmapError, match="champs interdits"):
        RoadmapService(roadmap, evidence_override=override).load()


def test_missing_override_preserves_base_roadmap(tmp_path):
    roadmap = _base_roadmap(tmp_path)
    result = RoadmapService(roadmap, evidence_override=tmp_path / "missing.json").load()
    assert result["evidence_date"] == "2026-08-20"
    assert result["modules"][0]["justification"] == "old"
