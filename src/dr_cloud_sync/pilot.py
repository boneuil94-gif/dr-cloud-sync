"""Five-item, create-only ShopCaisse pilot with hard safety barriers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .shopcaisse import ShopCaisseClient, ShopCaisseError, _text, _value

CONFIRMATION = "IMPORT-5"
MAX_CREATIONS = 5
ALLOWED_CLASSIFICATION = "PRET_A_CREER"


class PilotSafetyError(ShopCaisseError):
    """A pilot invariant was violated before an unsafe request could be sent."""


class CreationLimiter:
    """The only gateway to POST; its counter makes a sixth attempt impossible."""

    def __init__(self, client: ShopCaisseClient) -> None:
        self.client = client
        self.count = 0

    def create(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.count >= MAX_CREATIONS:
            raise PilotSafetyError("Plafond absolu de 5 créations atteint")
        # Count attempts before I/O: failures and loops can never open a sixth POST.
        self.count += 1
        return self.client.create_company_item(company_id, payload)


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    selections = manifest.get("selections") if isinstance(manifest, dict) else None
    if not isinstance(selections, list) or len(selections) != MAX_CREATIONS:
        raise PilotSafetyError("Le manifeste doit contenir exactement 5 sélections")
    keys: set[tuple[Any, Any]] = set()
    for row in selections:
        if row.get("classification_dry_run") != ALLOWED_CLASSIFICATION:
            raise PilotSafetyError("Seules les entrées PRET_A_CREER sont autorisées")
        if row.get("shopcaisse_id_dry_run") is not None:
            raise PilotSafetyError("Une sélection correspond déjà potentiellement à ShopCaisse")
        key = (row.get("prestashop_id"), row.get("combination_id"))
        if key in keys:
            raise PilotSafetyError("Sélection PrestaShop dupliquée dans le manifeste")
        keys.add(key)
        payload = row.get("payload_valide_dry_run")
        if not isinstance(payload, dict) or set(payload) - {"name", "price", "barcode", "reference"}:
            raise PilotSafetyError("Payload du dry-run absent ou non autorisé")
        if payload.get("name") != row.get("nom_prevu") or payload.get("price") != row.get("prix_prevu"):
            raise PilotSafetyError("Le payload ne correspond pas aux valeurs auditées")
    return manifest


def duplicate_candidate(items: list[dict[str, Any]], selection: dict[str, Any]) -> dict[str, Any] | None:
    """Return an exact identifier/name candidate; empty values never match."""
    expected_name = _text(selection["nom_prevu"])
    expected_ean = str(selection.get("ean_prevu") or "").strip()
    expected_reference = str(selection.get("reference_prevue") or "").strip()
    for item in items:
        barcodes = item.get("barcodes")
        values = ([str(x).strip() for x in barcodes] if isinstance(barcodes, list) else [])
        scalar_barcode = _value(item, "ean", "ean13", "barcode", "code_barre")
        if scalar_barcode:
            values.append(scalar_barcode)
        if expected_ean and expected_ean in values:
            return item
        if expected_reference and expected_reference == _value(item, "reference", "sku", "ref"):
            return item
        if expected_name and expected_name == _text(_value(item, "name", "product", "nom", "label")):
            return item
    return None


def run_pilot(api_key: str, confirm: str, company_id: str, manifest_path: Path,
              report_path: Path, *, client: ShopCaisseClient | None = None) -> dict[str, Any]:
    """Execute the gated pilot. No PrestaShop call and no method other than GET/POST exists here."""
    if confirm != CONFIRMATION:
        raise PilotSafetyError("Confirmation incorrecte: zéro écriture autorisée")
    if not company_id:
        raise PilotSafetyError("SHOPCAISSE_COMPANY_ID est absent")
    manifest = load_manifest(manifest_path)
    active_client = client or ShopCaisseClient(api_key)
    limiter = CreationLimiter(active_client)
    results = []
    report_path.parent.mkdir(parents=True, exist_ok=True)
    for selection in manifest["selections"]:
        result = {
            "prestashop_id": selection["prestashop_id"],
            "combination_id": selection.get("combination_id"),
            "nom_prevu": selection["nom_prevu"], "prix_prevu": selection["prix_prevu"],
            "ean_prevu": selection.get("ean_prevu"), "statut": "FAILED",
            "shopcaisse_id_cree": None, "nom_relu": None, "prix_relu": None,
            "ean_relu": None, "difference": None,
        }
        try:
            # A fresh full GET immediately before every potential creation.
            candidate = duplicate_candidate(active_client.pull_company_items(company_id), selection)
            if candidate is not None:
                result["statut"] = "SKIPPED"
                result["difference"] = "candidat ShopCaisse existant détecté; aucune modification"
            else:
                created = limiter.create(company_id, dict(selection["payload_valide_dry_run"]))
                item_id = str(created["id"])
                reread = active_client.get_company_item(company_id, item_id)
                result.update({"statut": "CREATED", "shopcaisse_id_cree": item_id,
                               "nom_relu": _value(reread, "name", "nom", "label"),
                               "prix_relu": reread.get("defaultPrice", reread.get("price"))})
                barcodes = reread.get("barcodes")
                result["ean_relu"] = (str(barcodes[0]) if isinstance(barcodes, list) and barcodes else
                                      _value(reread, "ean", "ean13", "barcode") or None)
                differences = [field for field, planned, actual in (
                    ("nom", result["nom_prevu"], result["nom_relu"]),
                    ("prix", result["prix_prevu"], result["prix_relu"]),
                    ("EAN", result["ean_prevu"], result["ean_relu"]),
                ) if planned != actual]
                result["difference"] = ", ".join(differences) or None
        except ShopCaisseError as exc:
            result["difference"] = str(exc)
        results.append(result)
        report_path.write_text(json.dumps({"mode": "IMPORT_PILOTE_5", "resultats": results},
                                          ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"mode": "IMPORT_PILOTE_5", "resultats": results}
