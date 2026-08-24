"""Read and validate the audited Roadmap V3 scorecard."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any


class RoadmapError(ValueError):
    """Raised when the machine-readable roadmap violates its contract."""


class RoadmapService:
    """Expose audited score facts plus a bounded non-score evidence override."""

    ALLOWED_STATUSES = frozenset(
        {"DONE_PROVEN", "DONE_CODE_ONLY", "PARTIAL", "BLOCKED", "TODO", "NOT_CONFIGURED"}
    )
    EVIDENCE_LEVELS = frozenset(
        {"CODE", "WIRED", "TESTED", "PRODUCTION_PROVEN", "PARTIAL", "BLOCKED", "NOT_CONFIGURED"}
    )
    OVERRIDE_MODULE_FIELDS = frozenset({"justification", "blocker", "next_step", "evidence_level", "status"})
    OVERRIDE_TOP_LEVEL_FIELDS = frozenset({"version", "evidence_date", "module_updates", "priorities", "blockers", "evidence"})

    LEGACY_PATH = Path("/app/docs/drcloud-os-roadmap.json")

    def __init__(self, path: Path | str | None = None, evidence_override: Path | str | None = None):
        configured = path if path is not None else os.environ.get("DRCLOUD_ROADMAP")
        self.configured_path = Path(configured) if configured else None
        self.path = self.configured_path or DEFAULT_ROADMAP
        configured_override = evidence_override if evidence_override is not None else os.environ.get("DRCLOUD_ROADMAP_EVIDENCE_OVERRIDE")
        self.evidence_override = Path(configured_override) if configured_override else DEFAULT_EVIDENCE_OVERRIDE
        if self.path == self.LEGACY_PATH and DEFAULT_ROADMAP.exists():
            logging.getLogger("drcloud.roadmap").warning(
                "ROADMAP_LEGACY_PATH configured_path=%s effective_path=%s",
                self.path, DEFAULT_ROADMAP,
            )
            self.path = DEFAULT_ROADMAP

    def _load_override(self) -> dict[str, Any] | None:
        if not self.evidence_override.is_file():
            return None
        try:
            override = json.loads(self.evidence_override.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoadmapError(f"Override roadmap illisible: {exc}") from exc
        if not isinstance(override, dict) or override.get("version") != 1:
            raise RoadmapError("Override roadmap invalide")
        forbidden_top = set(override) - self.OVERRIDE_TOP_LEVEL_FIELDS
        if forbidden_top:
            raise RoadmapError(f"Override roadmap contient des champs interdits: {sorted(forbidden_top)}")
        module_updates = override.get("module_updates", {})
        if not isinstance(module_updates, dict):
            raise RoadmapError("module_updates doit être un objet")
        for module_id, updates in module_updates.items():
            if not isinstance(module_id, str) or not isinstance(updates, dict):
                raise RoadmapError("module_updates invalide")
            forbidden = set(updates) - self.OVERRIDE_MODULE_FIELDS
            if forbidden:
                raise RoadmapError(f"Override module {module_id} contient des champs interdits: {sorted(forbidden)}")
        if "priorities" in override and not isinstance(override["priorities"], list):
            raise RoadmapError("priorities override doit être une liste")
        if "blockers" in override and not isinstance(override["blockers"], list):
            raise RoadmapError("blockers override doit être une liste")
        return override

    def _apply_override(self, roadmap: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
        if not override:
            return roadmap
        result = dict(roadmap)
        modules = [dict(module) for module in roadmap["modules"]]
        by_id = {module["id"]: module for module in modules}
        for module_id, updates in override.get("module_updates", {}).items():
            if module_id not in by_id:
                raise RoadmapError(f"Override cible un module inconnu: {module_id}")
            by_id[module_id].update(updates)
        result["modules"] = modules
        for field in ("evidence_date", "priorities", "blockers"):
            if field in override:
                result[field] = override[field]
        if "evidence" in override:
            result["evidence_update"] = override["evidence"]
        return result

    def load(self) -> dict[str, Any]:
        try:
            roadmap = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoadmapError(f"Roadmap illisible: {exc}") from exc
        self.validate(roadmap)
        roadmap = self._apply_override(roadmap, self._load_override())
        self.validate(roadmap)
        # Compatibility aliases for the dashboard and older read-only clients.
        result = dict(roadmap)
        result["global_progress_percent"] = roadmap["global_score"]
        result["remaining_percent"] = 100 - roadmap["global_score"]
        return result

    def diagnostic(self) -> dict[str, Any]:
        """Return an operator-safe view of roadmap path resolution and validity."""
        diagnostic = {
            "configured_path": str(self.configured_path) if self.configured_path else None,
            "effective_path": str(self.path),
            "file_exists": self.path.is_file(),
            "version": None,
            "status": "MISSING",
        }
        if not diagnostic["file_exists"]:
            return diagnostic
        try:
            roadmap = json.loads(self.path.read_text(encoding="utf-8"))
            diagnostic["version"] = roadmap.get("version") if isinstance(roadmap, dict) else None
            self.validate(roadmap)
            roadmap = self._apply_override(roadmap, self._load_override())
            self.validate(roadmap)
        except (OSError, json.JSONDecodeError, RoadmapError, AttributeError, TypeError):
            diagnostic["status"] = "INVALID"
            return diagnostic
        diagnostic["status"] = "OK"
        return diagnostic

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


DEFAULT_ROADMAP = Path(__file__).resolve().parents[2] / "config" / "roadmap_v3.json"
DEFAULT_EVIDENCE_OVERRIDE = Path(__file__).resolve().parents[2] / "config" / "roadmap_status_2026-08-24.json"
