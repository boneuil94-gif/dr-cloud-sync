"""Controlled, create-only ShopCaisse import built on the validated pilot barriers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .pilot import PilotSafetyError, duplicate_candidate
from .shopcaisse import ShopCaisseClient, ShopCaisseError, _value, validate_create_item

CONFIRMATION = "IMPORT-20"
MAX_CREATIONS = 20
ALLOWED_CLASSIFICATION = "PRET_A_CREER"


def _report() -> dict[str, Any]:
    return {"limite_creations": MAX_CREATIONS, "creations_effectuees": 0,
            "skipped": 0, "failed": 0, "arret_sur_echec": False, "resultats": []}


def _all_report(candidates: int = 0) -> dict[str, Any]:
    return {"mode": "IMPORT_ALL", "candidats": candidates, "creations_effectuees": 0,
            "skipped": 0, "failed": 0, "arret_sur_echec": False, "termine": False,
            "resultats": []}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _barcode_valid(value: Any) -> bool:
    if not isinstance(value, str) or not value.isdigit() or len(value) not in {8, 12, 13, 14}:
        return False
    digits = [int(char) for char in value]
    expected = (10 - sum(digit * (3 if (len(digits) - index) % 2 == 0 else 1)
                         for index, digit in enumerate(digits[:-1])) % 10) % 10
    return digits[-1] == expected


def validate_controlled_payload(payload: Any, company_id: str) -> list[str]:
    """Apply CreateSimpleItemDto and stricter finite-price/EAN checks before POST."""
    if not isinstance(payload, dict):
        return ["payload CreateSimpleItemDto"]
    allowed = {"name", "price", "barcode", "reference"}
    errors = validate_create_item(payload, company_id)
    if set(payload) - allowed:
        errors.append("champs non autorisés")
    price = payload.get("price")
    if isinstance(price, (int, float)) and not isinstance(price, bool) and not math.isfinite(price):
        errors.append("price")
    if "barcode" in payload and not _barcode_valid(payload["barcode"]):
        errors.append("barcode/EAN")
    return list(dict.fromkeys(errors))


def load_candidates(path: Path) -> list[dict[str, Any]]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    entries = plan.get("entrees") if isinstance(plan, dict) else None
    if not isinstance(entries, list):
        raise PilotSafetyError("Plan d'import PrestaShop invalide")
    return [row for row in entries if isinstance(row, dict)
            and row.get("action_prevue") == ALLOWED_CLASSIFICATION]


def _selection(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("champs_qui_seraient_crees")
    return {
        "prestashop_id": row.get("product_id"), "combination_id": row.get("combination_id"),
        "nom_prevu": payload.get("name") if isinstance(payload, dict) else None,
        "prix_prevu": payload.get("price") if isinstance(payload, dict) else None,
        "ean_prevu": payload.get("barcode") if isinstance(payload, dict) else None,
        "reference_prevue": payload.get("reference") if isinstance(payload, dict) else None,
    }


def _barcode(item: dict[str, Any]) -> str | None:
    values = item.get("barcodes")
    if isinstance(values, list) and values:
        return str(values[0])
    return _value(item, "ean", "ean13", "barcode", "code_barre") or None


def run_controlled_import(api_key: str, prestashop_api_key: str, confirm: str, company_id: str,
                          plan_path: Path, report_path: Path, *,
                          client: ShopCaisseClient | None = None) -> dict[str, Any]:
    """Create at most 20 new items, stopping at the first failed result."""
    report = _report()
    _write_report(report_path, report)
    if confirm != CONFIRMATION:
        raise PilotSafetyError("Confirmation incorrecte: zéro écriture autorisée")
    if not api_key:
        raise PilotSafetyError("SHOPCAISSE_API_KEY est absent: zéro écriture autorisée")
    if not prestashop_api_key:
        raise PilotSafetyError("PRESTASHOP_API_KEY est absent: zéro écriture autorisée")
    if not company_id:
        raise PilotSafetyError("SHOPCAISSE_COMPANY_ID est absent: zéro écriture autorisée")

    candidates = load_candidates(plan_path)
    active_client = client or ShopCaisseClient(api_key)
    for row in candidates:
        if report["creations_effectuees"] >= MAX_CREATIONS:
            break
        # Preserve the pilot's strongest duplicate barrier: refresh the full
        # catalogue immediately before every potential creation.
        catalogue = active_client.pull_company_items(company_id)
        selection = _selection(row)
        source = {"prestashop_id": row.get("product_id"),
                  "combination_id": row.get("combination_id"),
                  "reference": row.get("reference")}
        result = {"source": source, "statut": "FAILED", "shopcaisse_id": None,
                  "verification": {"name": False, "price": False, "barcode": False},
                  "erreur": None}
        existing = duplicate_candidate(catalogue, selection)
        if existing is not None:
            result.update({"statut": "SKIPPED", "shopcaisse_id": str(existing.get("id") or ""),
                           "erreur": "article ShopCaisse existant détecté; aucune modification"})
            report["skipped"] += 1
            report["resultats"].append(result)
            _write_report(report_path, report)
            continue

        payload = row.get("champs_qui_seraient_crees")
        errors = validate_controlled_payload(payload, company_id)
        if errors:
            result["erreur"] = "Payload CreateSimpleItemDto invalide: " + ", ".join(errors)
        else:
            try:
                created = active_client.create_company_item(company_id, dict(payload))
                report["creations_effectuees"] += 1
                item_id = str(created["id"])
                result["shopcaisse_id"] = item_id
                reread = active_client.get_company_item(company_id, item_id)
                actual_price = reread.get("defaultPrice", reread.get("price"))
                expected_barcode = payload.get("barcode")
                verification = {
                    "name": _value(reread, "name", "nom", "label") == payload["name"],
                    "price": actual_price == payload["price"],
                    "barcode": expected_barcode is None or _barcode(reread) == expected_barcode,
                }
                result["verification"] = verification
                if all(verification.values()):
                    result["statut"] = "CREATED"
                else:
                    result["erreur"] = "Relecture ShopCaisse non conforme: " + ", ".join(
                        key for key, valid in verification.items() if not valid)
            except (ShopCaisseError, KeyError, TypeError, ValueError) as exc:
                result["erreur"] = str(exc)

        report["resultats"].append(result)
        if result["statut"] == "FAILED":
            report["failed"] += 1
            report["arret_sur_echec"] = True
            _write_report(report_path, report)
            return report
        _write_report(report_path, report)
    return report


def run_all_import(api_key: str, prestashop_api_key: str, confirm: str, company_id: str,
                   plan_path: Path, report_path: Path, *,
                   client: ShopCaisseClient | None = None) -> dict[str, Any]:
    """Run the validated create-only import over every PRET_A_CREER candidate."""
    report = _all_report()
    _write_report(report_path, report)
    if confirm != "IMPORT-ALL":
        raise PilotSafetyError("Confirmation incorrecte: zéro écriture autorisée")
    if not api_key:
        raise PilotSafetyError("SHOPCAISSE_API_KEY est absent: zéro écriture autorisée")
    if not prestashop_api_key:
        raise PilotSafetyError("PRESTASHOP_API_KEY est absent: zéro écriture autorisée")
    if not company_id:
        raise PilotSafetyError("SHOPCAISSE_COMPANY_ID est absent: zéro écriture autorisée")

    candidates = load_candidates(plan_path)
    report["candidats"] = len(candidates)
    _write_report(report_path, report)
    active_client = client or ShopCaisseClient(api_key)
    # Mandatory catalogue snapshot before the first possible creation. Newly
    # created items are appended immediately, keeping the barrier active even
    # when two plan entries represent the same item.
    catalogue = active_client.pull_company_items(company_id)
    for row in candidates:
        selection = _selection(row)
        source = {"prestashop_id": row.get("product_id"),
                  "combination_id": row.get("combination_id"),
                  "reference": row.get("reference")}
        result = {"source": source, "statut": "FAILED", "shopcaisse_id": None,
                  "verification": {"name": False, "price": False, "barcode": False},
                  "erreur": None}
        existing = duplicate_candidate(catalogue, selection)
        if existing is not None:
            result.update({"statut": "SKIPPED", "shopcaisse_id": str(existing.get("id") or ""),
                           "erreur": "article ShopCaisse existant détecté; aucune modification"})
            report["skipped"] += 1
            report["resultats"].append(result)
            _write_report(report_path, report)
            continue

        payload = row.get("champs_qui_seraient_crees")
        errors = validate_controlled_payload(payload, company_id)
        if row.get("action_prevue") != ALLOWED_CLASSIFICATION:
            errors.append("classification PRET_A_CREER")
        if errors:
            result["erreur"] = "Payload CreateSimpleItemDto invalide: " + ", ".join(errors)
        else:
            try:
                created = active_client.create_company_item(company_id, dict(payload))
                report["creations_effectuees"] += 1
                item_id = str(created["id"])
                result["shopcaisse_id"] = item_id
                catalogue.append(created)
                reread = active_client.get_company_item(company_id, item_id)
                actual_price = reread.get("defaultPrice", reread.get("price"))
                expected_barcode = payload.get("barcode")
                verification = {
                    "name": _value(reread, "name", "nom", "label") == payload["name"],
                    "price": actual_price == payload["price"],
                    "barcode": expected_barcode is None or _barcode(reread) == expected_barcode,
                }
                result["verification"] = verification
                if all(verification.values()):
                    result["statut"] = "CREATED"
                else:
                    result["erreur"] = "Relecture ShopCaisse non conforme: " + ", ".join(
                        key for key, valid in verification.items() if not valid)
            except (ShopCaisseError, KeyError, TypeError, ValueError) as exc:
                result["erreur"] = str(exc)

        report["resultats"].append(result)
        if result["statut"] == "FAILED":
            report["failed"] += 1
            report["arret_sur_echec"] = True
            _write_report(report_path, report)
            return report
        _write_report(report_path, report)
    report["termine"] = True
    _write_report(report_path, report)
    return report
