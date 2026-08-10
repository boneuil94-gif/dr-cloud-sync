"""Read and validate the audited Roadmap V3 scorecard."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class RoadmapError(ValueError):
    """Raised when the machine-readable roadmap violates its contract."""


class RoadmapService:
    """Expose the scorecard verbatim; scores are audit facts, not calculations."""

    ALLOWED_STATUSES = frozenset(
        {"DONE_PROVEN", "DONE_CODE_ONLY", "PARTIAL", "BLOCKED", "TODO", "NOT_CONFIGURED"}
    )
    EVIDENCE_LEVELS = frozenset(
        {"CODE", "WIRED", "TESTED", "PRODUCTION_PROVEN", "PARTIAL", "BLOCKED", "NOT_CONFIGURED"}
    )

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            roadmap = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoadmapError(f"Roadmap illisible: {exc}") from exc
        self.validate(roadmap)
        # Compatibility aliases for the dashboard and older read-only clients.
        result = dict(roadmap)
        result["global_progress_percent"] = roadmap["global_score"]
        result["remaining_percent"] = 100 - roadmap["global_score"]
        return result

    def validate(self, roadmap: dict[str, Any]) -> None:
        if roadmap.get("version") != 3:
            raise RoadmapError("La source doit être la Roadmap V3")
        score = roadmap.get("global_score")
        if not isinstance(score, int) or not 0 <= score <= 100:
            raise RoadmapError("global_score doit être un entier entre 0 et 100")
        dimensions = roadmap.get("dimensions")
        if not isinstance(dimensions, dict) or not dimensions:
            raise RoadmapError("dimensions doit être un objet non vide")
        if any(not isinstance(value, int) or not 0 <= value <= 100 for value in dimensions.values()):
            raise RoadmapError("Score de dimension invalide")
        modules = roadmap.get("modules")
        if not isinstance(modules, list) or not modules:
            raise RoadmapError("modules doit être une liste non vide")
        required = {"id", "name", "score", "status", "justification", "blocker", "next_step", "evidence_level"}
        for module in modules:
            if not isinstance(module, dict) or not required <= module.keys():
                raise RoadmapError(f"Module incomplet: {module.get('id') if isinstance(module, dict) else '?' }")
            if not isinstance(module["score"], int) or not 0 <= module["score"] <= 100:
                raise RoadmapError(f"Score invalide pour {module['id']}")
            if module["status"] not in self.ALLOWED_STATUSES:
                raise RoadmapError(f"Statut invalide pour {module['id']}")
            if module["evidence_level"] not in self.EVIDENCE_LEVELS:
                raise RoadmapError(f"Niveau de preuve invalide pour {module['id']}")
        if not roadmap.get("evidence_date") or not isinstance(roadmap.get("priorities"), list) or not isinstance(roadmap.get("blockers"), list):
            raise RoadmapError("evidence_date, priorities et blockers sont requis")


DEFAULT_ROADMAP = Path(os.environ.get(
    "DRCLOUD_ROADMAP",
    Path(__file__).resolve().parents[2] / "config" / "roadmap_v3.json",
))
