"""Roadmap domain service: validation and progress calculations, without infrastructure."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class RoadmapError(ValueError):
    """Raised when the machine-readable roadmap violates its contract."""


class RoadmapService:
    ALLOWED_STATUSES = frozenset({"TODO", "IN_PROGRESS", "DONE", "BLOCKED"})

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            roadmap = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoadmapError(f"Roadmap illisible: {exc}") from exc
        self.validate(roadmap)
        # Values exposed to callers always come from milestones and weights.
        for module in roadmap["modules"]:
            module["status"] = self.module_status(module)
            module["progress_percent"] = self.module_progress(module)
            module["weighted_progress"] = round(
                module["weight"] * module["progress_percent"] / 100, 2
            )
        roadmap["global_progress_percent"] = self.global_progress(roadmap)
        roadmap["remaining_percent"] = self.remaining(roadmap)
        return roadmap

    def validate(self, roadmap: dict[str, Any]) -> None:
        modules = roadmap.get("modules")
        if not isinstance(modules, list) or not modules:
            raise RoadmapError("modules doit être une liste non vide")
        if sum(module.get("weight", 0) for module in modules) != 100:
            raise RoadmapError("Le poids total des modules doit être égal à 100")
        for module in modules:
            milestones = module.get("milestones")
            if not isinstance(milestones, list) or not milestones:
                raise RoadmapError(f"Jalons invalides pour {module.get('id')}")
            for milestone in milestones:
                if not isinstance(milestone, dict) or not {"id", "name", "status"} <= milestone.keys():
                    raise RoadmapError(f"Jalon invalide pour {module.get('id')}")
                if milestone["status"] not in self.ALLOWED_STATUSES:
                    raise RoadmapError(f"Statut de jalon invalide: {milestone['status']}")
                steps = milestone.get("steps")
                if milestone["status"] == "IN_PROGRESS":
                    if not isinstance(steps, list) or len(steps) < 2:
                        raise RoadmapError(f"Sous-étapes requises pour {milestone['id']}")
                    if any(not isinstance(step, dict) or not {"name", "done"} <= step.keys()
                           or not isinstance(step["done"], bool) for step in steps):
                        raise RoadmapError(f"Sous-étape invalide pour {milestone['id']}")
                    if all(step["done"] for step in steps) or not any(step["done"] for step in steps):
                        raise RoadmapError(f"État IN_PROGRESS incohérent pour {milestone['id']}")
            progress = self.module_progress(module)
            if not 0 <= progress <= 100:
                raise RoadmapError(f"Progression hors limites pour {module.get('id')}")

    def module_progress(self, module: dict[str, Any]) -> float:
        milestones = module.get("milestones", [])
        if not milestones:
            raise RoadmapError("Un module doit avoir au moins un jalon")
        earned = sum(self.milestone_credit(item) for item in milestones)
        return round(100 * earned / len(milestones), 2)

    def milestone_credit(self, milestone: dict[str, Any]) -> float:
        status = milestone.get("status")
        if status == "DONE":
            return 1.0
        if status in {"TODO", "BLOCKED"}:
            return 0.0
        if status == "IN_PROGRESS":
            steps = milestone.get("steps", [])
            if not steps:
                raise RoadmapError("Un jalon IN_PROGRESS doit avoir des sous-étapes")
            return sum(step.get("done") is True for step in steps) / len(steps)
        raise RoadmapError("Statut de jalon invalide")

    def module_status(self, module: dict[str, Any]) -> str:
        statuses = {item["status"] for item in module["milestones"]}
        if statuses == {"DONE"}:
            return "DONE"
        if statuses <= {"TODO", "BLOCKED"}:
            return "BLOCKED" if "BLOCKED" in statuses and "TODO" not in statuses else "TODO"
        return "IN_PROGRESS"

    def global_progress(self, roadmap: dict[str, Any]) -> float:
        self._validate_weights(roadmap)
        return round(sum(
            module["weight"] * self.module_progress(module) / 100
            for module in roadmap["modules"]
        ), 2)

    def remaining(self, roadmap: dict[str, Any]) -> float:
        return round(100 - self.global_progress(roadmap), 2)

    @staticmethod
    def _validate_weights(roadmap: dict[str, Any]) -> None:
        if sum(module.get("weight", 0) for module in roadmap.get("modules", [])) != 100:
            raise RoadmapError("Le poids total des modules doit être égal à 100")


DEFAULT_ROADMAP = Path(os.environ.get(
    "DRCLOUD_ROADMAP",
    Path(__file__).resolve().parents[2] / "docs" / "drcloud-os-roadmap.json",
))
