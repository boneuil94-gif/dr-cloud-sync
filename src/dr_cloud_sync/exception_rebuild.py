"""Explicit, resumable rebuild of the 34 approved mapping exceptions."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .controlled_import import validate_controlled_payload, _barcode
from .config import resolve_prestashop_api_url
from .pilot import PilotSafetyError
from .shopcaisse import ShopCaisseClient, ShopCaisseError, _value

EXPECTED = {"PROBABLE": 14, "NON_TROUVEE": 20}
MAX_CREATIONS = 34
CONFIRMATION = "CREATE-34"


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_exceptions(path: Path) -> list[dict[str, Any]]:
    """Load only the explicitly approved classes and enforce the exact frozen totals."""
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("exceptions") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise PilotSafetyError("Rapport d'exceptions invalide: zéro écriture autorisée")
    selected = [row for row in rows if isinstance(row, dict)
                and row.get("classification_apres") in EXPECTED]
    counts = {name: sum(row.get("classification_apres") == name for row in selected)
              for name in EXPECTED}
    keys = [row.get("prestashop_key") for row in selected]
    if len(selected) != MAX_CREATIONS or counts != EXPECTED or len(set(keys)) != len(keys) or not all(keys):
        raise PilotSafetyError(
            f"Exceptions attendues 34 (PROBABLE=14, NON_TROUVEE=20), reçues {len(selected)}: zéro écriture autorisée"
        )
    return selected


def _empty_report() -> dict[str, Any]:
    return {"attendues": 34, "created": 0, "skipped_already_created": 0,
            "failed": 0, "complete": False, "resultats": []}


def _load_corrections(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "corrections": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("corrections"), dict):
        raise PilotSafetyError("Fichier de corrections invalide: zéro écriture autorisée")
    return value


def _valid_correction(value: Any) -> bool:
    return (isinstance(value, dict) and bool(str(value.get("shopcaisse_item_id", "")).strip())
            and value.get("source") == "EXCEPTION_REBUILD")


def _payload(unit: dict[str, Any]) -> dict[str, Any]:
    name = str(unit.get("name") or "").strip()
    attributes = [str(value).strip() for value in unit.get("attributes", []) if str(value).strip()]
    if name and unit.get("combination_id") not in (None, "", 0, "0") and attributes:
        name += " - " + " / ".join(attributes)
    payload: dict[str, Any] = {"name": name, "price": unit.get("price_ttc")}
    if unit.get("ean") not in (None, ""):
        payload["barcode"] = str(unit["ean"]).strip()
    if unit.get("reference") not in (None, ""):
        payload["reference"] = str(unit["reference"]).strip()
    return payload


def run_exception_rebuild(api_key: str, prestashop_api_key: str, confirm: str, company_id: str,
                          exceptions_path: Path, corrections_path: Path, report_path: Path, *,
                          prestashop_loader: Callable[[str], list[dict[str, Any]]],
                          prestashop_api_url: str | None = None,
                          client: ShopCaisseClient | None = None) -> dict[str, Any]:
    """Create only approved exceptions; persist a correction immediately after verified GET."""
    if confirm != CONFIRMATION:
        raise PilotSafetyError("Confirmation incorrecte: zéro écriture autorisée")
    exceptions = load_exceptions(exceptions_path)  # validate before any remote operation
    if not api_key or not prestashop_api_key:
        raise PilotSafetyError("Secret API absent: zéro écriture autorisée")
    if not company_id:
        raise PilotSafetyError("SHOPCAISSE_COMPANY_ID absent: zéro écriture autorisée")

    resolved_url = resolve_prestashop_api_url(prestashop_api_url)
    report = _empty_report()
    _write(report_path, report)
    corrections = _load_corrections(corrections_path)
    _write(corrections_path, corrections)
    current = {str(unit.get("key")): unit for unit in prestashop_loader(resolved_url)}
    active = client or ShopCaisseClient(api_key)
    for exception in exceptions:
        key = str(exception["prestashop_key"])
        unit = current.get(key, {})
        result = {"prestashop_key": key, "product_id": exception.get("product_id"),
                  "combination_id": exception.get("combination_id"), "name": unit.get("name"),
                  "classification_origine": exception["classification_apres"], "statut": "FAILED",
                  "shopcaisse_item_id": None,
                  "verification": {"name": False, "price": False, "barcode": False}, "erreur": None}
        existing = corrections["corrections"].get(key)
        if _valid_correction(existing):
            result.update(statut="SKIPPED_ALREADY_CREATED",
                          shopcaisse_item_id=str(existing["shopcaisse_item_id"]),
                          verification={"name": True, "price": True, "barcode": True})
            report["skipped_already_created"] += 1
            report["resultats"].append(result)
            _write(report_path, report)
            continue
        payload = _payload(unit)
        errors = validate_controlled_payload(payload, company_id)
        if not math.isfinite(payload.get("price")) if isinstance(payload.get("price"), float) else False:
            errors.append("price")
        if not unit or errors:
            result["erreur"] = "Données PrestaShop/payload invalides: " + ", ".join(errors or ["entrée absente"])
        else:
            try:
                created = active.create_company_item(company_id, payload)
                item_id = str(created["id"])
                result["shopcaisse_item_id"] = item_id
                reread = active.get_company_item(company_id, item_id)
                expected_barcode = payload.get("barcode")
                checks = {"name": _value(reread, "name", "nom", "label") == payload["name"],
                          "price": reread.get("defaultPrice", reread.get("price")) == payload["price"],
                          "barcode": expected_barcode is None or _barcode(reread) == expected_barcode}
                result["verification"] = checks
                if all(checks.values()):
                    result["statut"] = "CREATED"
                    report["created"] += 1
                    corrections["corrections"][key] = {
                        "shopcaisse_item_id": item_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source": "EXCEPTION_REBUILD",
                    }
                    _write(corrections_path, corrections)
                else:
                    result["erreur"] = "Relecture ShopCaisse non conforme: " + ", ".join(k for k, v in checks.items() if not v)
            except (ShopCaisseError, KeyError, TypeError, ValueError) as exc:
                result["erreur"] = str(exc)
        report["resultats"].append(result)
        if result["statut"] == "FAILED":
            report["failed"] += 1
            _write(report_path, report)
            return report
        _write(report_path, report)
    report["complete"] = all(_valid_correction(corrections["corrections"].get(row["prestashop_key"]))
                             for row in exceptions)
    _write(report_path, report)
    return report


def build_final_mapping(mapping_path: Path, exceptions_path: Path, corrections_path: Path,
                        output_path: Path) -> dict[str, Any]:
    """Merge verified correction records into the original mapping without remote writes."""
    original = json.loads(mapping_path.read_text(encoding="utf-8"))
    exceptions = load_exceptions(exceptions_path)
    corrections = _load_corrections(corrections_path)["corrections"]
    exception_keys = {row["prestashop_key"] for row in exceptions}
    mappings = []
    for row in original.get("mappings", []):
        key = row.get("prestashop", {}).get("key")
        if row.get("classification") == "CERTAINE":
            mappings.append(row)
        elif key in exception_keys and _valid_correction(corrections.get(key)):
            updated = dict(row)
            updated.update(classification="CERTAINE", methode="EXCEPTION_REBUILD", confidence=1.0)
            updated["shopcaisse"] = {"item_id": str(corrections[key]["shopcaisse_item_id"])}
            mappings.append(updated)
    counts = {name.lower(): sum(row.get("classification") == name for row in mappings)
              for name in ("CERTAINE", "PROBABLE", "AMBIGUE", "NON_TROUVEE", "CONFLIT")}
    document = {"prestashop_total": original.get("prestashop_total", len(original.get("mappings", []))),
                **counts, "complete": len(mappings) == 478 and counts["certaine"] == 478,
                "mappings": mappings}
    _write(output_path, document)
    return document
