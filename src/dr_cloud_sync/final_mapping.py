"""Read-only validation and finalisation of the PrestaShop/ShopCaisse mapping."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .shopcaisse import ShopCaisseClient, _value

EXPECTED_INITIAL = 444
EXPECTED_CORRECTIONS = 34
EXPECTED_TOTAL = 478
CLASSIFICATIONS = ("CERTAINE", "PROBABLE", "AMBIGUE", "NON_TROUVEE", "CONFLIT")


class FinalMappingError(ValueError):
    """A safety invariant failed; the catalogue must not be used for inventory."""


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _ps(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("prestashop")
    return nested if isinstance(nested, dict) else row


def _key(row: dict[str, Any]) -> str:
    source = _ps(row)
    return _text(source.get("key") or source.get("prestashop_key") or row.get("prestashop_key"))


def _item_id(row: dict[str, Any]) -> str:
    nested = row.get("shopcaisse")
    if isinstance(nested, dict):
        return _text(nested.get("item_id") or nested.get("id"))
    return _text(row.get("shopcaisse_item_id"))


def _corrections(document: Any) -> list[dict[str, Any]]:
    raw = document.get("corrections") if isinstance(document, dict) else None
    if isinstance(raw, dict):
        return [{"prestashop_key": key, **value} for key, value in raw.items()
                if isinstance(value, dict)]
    if isinstance(raw, list):
        return [value for value in raw if isinstance(value, dict)]
    raise FinalMappingError("mapping-corrections-exceptions.json invalide")


def _remote_barcode(item: dict[str, Any]) -> str:
    value = _value(item, "barcode", "ean", "ean13")
    if value:
        return _text(value)
    values = item.get("barcodes")
    if isinstance(values, list) and values:
        first = values[0]
        return _text(first.get("barcode") if isinstance(first, dict) else first)
    return ""


def _expected_name(row: dict[str, Any]) -> str:
    """Reproduce the name frozen by the approved exception creation payload."""
    source = _ps(row)
    name = _text(source.get("name"))
    attributes = [_text(value) for value in source.get("attributes", []) if _text(value)]
    if source.get("combination_id") not in (None, "", 0, "0") and attributes:
        name += " - " + " / ".join(attributes)
    return name


def _flat(row: dict[str, Any], item_id: str, *, correction: bool) -> dict[str, Any]:
    source = _ps(row)
    attributes = source.get("attributes") or source.get("attributs") or []
    values = []
    if isinstance(attributes, dict):
        values = [_text(value) for value in attributes.values() if _text(value)]
    elif isinstance(attributes, list):
        values = [_text(value.get("nom") or value.get("value") or value.get("label"))
                  if isinstance(value, dict) else _text(value) for value in attributes]
        values = [value for value in values if value]
    variant = _text(source.get("variant_name")) or " · ".join(values)
    base = _text(source.get("base_name") or source.get("name") or row.get("name"))
    return {
        "prestashop_key": _key(row),
        "product_id": source.get("product_id", row.get("product_id")),
        "combination_id": source.get("combination_id", row.get("combination_id")),
        "name": source.get("name", row.get("name")),
        "base_name": base,
        "variant_name": variant,
        "display_name": base + (f" — {variant}" if variant else ""),
        "attributes": attributes,
        "shopcaisse_name": _text(row.get("shopcaisse_name") or _expected_name(row)),
        "stock_source": source.get("stock", source.get("quantity")),
        "price": source.get("price", source.get("price_ttc")),
        "images": source.get("images") or source.get("image_ids") or [],
        "ean": source.get("ean", source.get("ean13", row.get("ean"))),
        "reference": source.get("reference", row.get("reference")),
        "shopcaisse_item_id": item_id,
        "classification": "CERTAINE",
        "methode": "EXCEPTION_REBUILD" if correction else row.get("methode"),
        **({"preuve": row["preuve"]} if "preuve" in row else {}),
    }


def _base_report() -> dict[str, Any]:
    return {
        "prestashop_total": EXPECTED_TOTAL, "mapping_total": 0, "certaine": 0,
        "probable": 0, "ambigue": 0, "non_trouvee": 0, "conflit": 0,
        "corrections_exceptions": 0, "missing_shopcaisse_item_id": 0,
        "duplicate_prestashop_keys": [], "duplicate_shopcaisse_item_ids": [],
        "shopcaisse_corrections_revalidated": 0, "ready_for_inventory": False,
        "errors": [],
    }


def finalize_mapping(mapping_path: Path, corrections_path: Path, creation_report_path: Path,
                     output_dir: Path, company_id: str, client: ShopCaisseClient) -> dict[str, Any]:
    """Validate 444+34 records using GET only and write final mapping/report/inventory."""
    report = _base_report()
    report_path = output_dir / "rapport-mapping-final.json"
    _write(report_path, report)
    try:
        if not _text(company_id):
            raise FinalMappingError("SHOPCAISSE_COMPANY_ID absent")
        original = json.loads(mapping_path.read_text(encoding="utf-8"))
        rows = original.get("mappings")
        if not isinstance(rows, list):
            raise FinalMappingError("mapping initial invalide")
        certain = [row for row in rows if row.get("classification") == "CERTAINE"]
        if len(certain) != EXPECTED_INITIAL:
            raise FinalMappingError(f"mapping initial CERTAINE attendu=444, reçu={len(certain)}")

        corrections = _corrections(json.loads(corrections_path.read_text(encoding="utf-8")))
        report["corrections_exceptions"] = len(corrections)
        if len(corrections) != EXPECTED_CORRECTIONS:
            raise FinalMappingError(f"corrections attendues=34, reçues={len(corrections)}")
        correction_keys = [_text(row.get("prestashop_key")) for row in corrections]
        correction_ids = [_text(row.get("shopcaisse_item_id")) for row in corrections]
        if not all(correction_keys) or len(set(correction_keys)) != EXPECTED_CORRECTIONS:
            raise FinalMappingError("les 34 prestashop_key de correction doivent être uniques")
        if not all(correction_ids):
            raise FinalMappingError("une correction ne contient pas de shopcaisse_item_id")

        initial_keys = [_key(row) for row in certain]
        overlap = sorted(set(initial_keys) & set(correction_keys))
        if overlap:
            raise FinalMappingError("une correction écrase un mapping CERTAINE initial: " + ", ".join(overlap))
        if len(set(initial_keys)) != EXPECTED_INITIAL or not all(initial_keys):
            raise FinalMappingError("prestashop_key initiale absente ou dupliquée")

        creation = json.loads(creation_report_path.read_text(encoding="utf-8"))
        if not (creation.get("attendues") == EXPECTED_CORRECTIONS
                and creation.get("created", 0) + creation.get("skipped_already_created", 0) == EXPECTED_CORRECTIONS
                and creation.get("failed") == 0 and creation.get("complete") is True):
            raise FinalMappingError("rapport de création incomplet ou en échec")

        by_key = {_key(row): row for row in rows}
        corrected_rows: list[dict[str, Any]] = []
        remote_by_id: dict[str, dict[str, Any]] = {}
        for correction in corrections:
            key, item_id = _text(correction["prestashop_key"]), _text(correction["shopcaisse_item_id"])
            source = by_key.get(key)
            if source is None:
                raise FinalMappingError(f"correction sans entrée PrestaShop: {key}")
            remote = client.get_company_item(company_id, item_id)  # strictly GET
            remote_id = _text(_value(remote, "item_id", "id"))
            if remote_id and remote_id != item_id:
                raise FinalMappingError(f"item_id ShopCaisse incohérent pour {key}")
            expected_name = _text(correction.get("name")) or _expected_name(source)
            actual_name = _text(_value(remote, "name", "nom", "label"))
            if expected_name and expected_name != actual_name:
                raise FinalMappingError(f"name ShopCaisse incohérent pour {key}")
            expected_ean = _text(correction.get("ean") or correction.get("barcode")
                                 or _ps(source).get("ean") or _ps(source).get("ean13"))
            actual_ean = _remote_barcode(remote)
            if expected_ean and expected_ean != actual_ean:
                raise FinalMappingError(f"EAN ShopCaisse incohérent pour {key}")
            remote_by_id[item_id] = remote
            corrected_rows.append(_flat(source, item_id, correction=True))
            report["shopcaisse_corrections_revalidated"] += 1

        final_rows = [_flat(row, _item_id(row), correction=False) for row in certain] + corrected_rows
        keys = [row["prestashop_key"] for row in final_rows]
        ids = [row["shopcaisse_item_id"] for row in final_rows]
        report["mapping_total"] = len(final_rows)
        report["certaine"] = sum(row["classification"] == "CERTAINE" for row in final_rows)
        report["missing_shopcaisse_item_id"] = sum(not value for value in ids)
        report["duplicate_prestashop_keys"] = sorted(key for key, count in Counter(keys).items() if count > 1)
        report["duplicate_shopcaisse_item_ids"] = sorted(item for item, count in Counter(ids).items()
                                                       if item and count > 1)
        required_ok = (len(final_rows) == EXPECTED_TOTAL and report["certaine"] == EXPECTED_TOTAL
                       and not report["missing_shopcaisse_item_id"]
                       and not report["duplicate_prestashop_keys"]
                       and report["shopcaisse_corrections_revalidated"] == EXPECTED_CORRECTIONS)
        report["ready_for_inventory"] = required_ok and not report["duplicate_shopcaisse_item_ids"]
        if not required_ok:
            raise FinalMappingError("contrôles finaux d'unicité ou de complétude en échec")

        mapping = {"prestashop_total": EXPECTED_TOTAL, "mapping_total": EXPECTED_TOTAL,
                   "certaine": EXPECTED_TOTAL, "probable": 0, "ambigue": 0,
                   "non_trouvee": 0, "conflit": 0, "mappings": final_rows}
        inventory = []
        for row in final_rows:
            source = by_key[row["prestashop_key"]]
            remote = remote_by_id.get(row["shopcaisse_item_id"], {})
            inventory.append({
                "prestashop_key": row["prestashop_key"], "product_id": row["product_id"],
                "combination_id": row["combination_id"], "nom complet": row["name"],
                "base_name": _ps(source).get("base_name") or _ps(source).get("name") or row["name"],
                "variant_name": _ps(source).get("variant_name") or "",
                "attributes": _ps(source).get("attributes") or {},
                "EAN": row["ean"], "référence": row["reference"],
                "shopcaisse_item_id": row["shopcaisse_item_id"],
                "stock_prestashop": _ps(source).get("stock", _ps(source).get("quantity")),
                "stock_shopcaisse": remote.get("stock", remote.get("quantity")),
                "quantite_physique": None, "ecart_prestashop": None, "ecart_shopcaisse": None,
                "inventaire_valide": False,
            })
        _write(output_dir / "mapping-prestashop-shopcaisse-final.json", mapping)
        _write(output_dir / "inventaire-initial-drcloud.json", inventory)
        _write(report_path, report)
        if not report["ready_for_inventory"]:
            raise FinalMappingError("item_id ShopCaisse partagé: analyse requise avant inventaire")
        return report
    except Exception as exc:
        report["ready_for_inventory"] = False
        report["errors"].append(str(exc))
        _write(report_path, report)
        raise
