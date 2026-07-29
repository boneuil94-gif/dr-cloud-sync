"""Construction en lecture seule du mapping durable PrestaShop -> ShopCaisse."""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .prestashop import PrestaShopClient
from .config import resolve_prestashop_api_url
from .shopcaisse import ShopCaisseClient


CLASSIFICATIONS = ("CERTAINE", "PROBABLE", "AMBIGUE", "NON_TROUVEE", "CONFLIT")


def prestashop_key(product_id: Any, combination_id: Any = None) -> str:
    """Return the stable source identifier, preserving a real combination id."""
    base = f"prestashop:{product_id}"
    return f"{base}:{combination_id}" if combination_id not in (None, "", 0, "0") else base


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("language", value)
    if isinstance(value, list):
        value = next((v.get("value", v.get("#text", "")) if isinstance(v, dict) else v
                      for v in value if v), "")
    if isinstance(value, dict):
        value = value.get("value", value.get("#text", ""))
    return str(value).strip()


def _norm(value: Any) -> str:
    value = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _code(value: Any) -> str:
    return re.sub(r"[\s-]", "", _text(value)).upper()


def _price(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _coherent(a: Any, b: Any) -> bool:
    left, right = _price(a), _price(b)
    return left is not None and right is not None and abs(left - right) <= 0.02


def build_mapping(prestashop: list[dict[str, Any]], shopcaisse: list[dict[str, Any]],
                  reports: Iterable[dict[str, Any]] = ()) -> tuple[dict[str, Any], dict[str, Any]]:
    """Match normalized catalogues without changing either remote system."""
    by_id = {str(s.get("item_id")): s for s in shopcaisse if s.get("item_id") not in (None, "")}
    indexes: dict[str, dict[str, list[dict[str, Any]]]] = {
        field: defaultdict(list) for field in ("ean", "reference", "name")
    }
    for item in shopcaisse:
        indexes["ean"][_code(item.get("ean"))].append(item)
        indexes["reference"][_code(item.get("reference"))].append(item)
        indexes["name"][_norm(item.get("name"))].append(item)
    known: dict[str, str] = {}
    for report in reports:
        for row in report.get("resultats", report.get("results", [])):
            status = str(row.get("statut", row.get("status", ""))).upper()
            item_id = (row.get("shopcaisse_id") or row.get("shopcaisse_id_cree") or
                       row.get("item_id") or row.get("existing_item_id"))
            key = row.get("prestashop_key") or row.get("source_key") or row.get("key")
            source = row.get("source") if isinstance(row.get("source"), dict) else row
            product_id = source.get("product_id", source.get("prestashop_id"))
            if not key and product_id is not None:
                key = prestashop_key(product_id, source.get("combination_id"))
            if status in {"CREATED", "SKIPPED"} and key and item_id:
                known[str(key)] = str(item_id)

    mappings = []
    for source in prestashop:
        key = source.get("key") or prestashop_key(source.get("product_id"), source.get("combination_id"))
        ean = indexes["ean"].get(_code(source.get("ean")), []) if _code(source.get("ean")) else []
        ref = indexes["reference"].get(_code(source.get("reference")), []) if _code(source.get("reference")) else []
        target = by_id.get(known.get(key, ""))
        classification, method, confidence = "NON_TROUVEE", "NONE", 0.0
        candidates: list[dict[str, Any]] = []
        if target:
            classification, method, confidence = "CERTAINE", "IMPORT_REPORT", 1.0
        elif len(ean) == 1 and len(ref) == 1 and ean[0].get("item_id") != ref[0].get("item_id"):
            classification, method, confidence, candidates = "CONFLIT", "EAN_REFERENCE_CONFLICT", 0.0, ean + ref
        elif len(ean) > 1:
            classification, method, confidence, candidates = "AMBIGUE", "EAN_DUPLICATE", 0.5, ean
        elif len(ean) == 1:
            target, classification, method, confidence = ean[0], "CERTAINE", "EAN", 1.0
        elif len(ref) > 1:
            classification, method, confidence, candidates = "AMBIGUE", "REFERENCE_DUPLICATE", 0.5, ref
        elif len(ref) == 1:
            target, classification, method, confidence = ref[0], "CERTAINE", "REFERENCE", 1.0
        else:
            named = [s for s in indexes["name"].get(_norm(source.get("name")), [])
                     if _coherent(source.get("price_ttc"), s.get("price_ttc"))]
            if len(named) == 1:
                target, classification, method, confidence = named[0], "PROBABLE", "NAME_PRICE", 0.75
            elif len(named) > 1:
                classification, method, confidence, candidates = "AMBIGUE", "NAME_PRICE_DUPLICATE", 0.4, named
        public_source = {k: source.get(k) for k in
                         ("key", "product_id", "combination_id", "name", "attributes", "ean",
                          "reference", "price_ttc", "stock")}
        public_source["key"] = key
        public_target = ({k: target.get(k) for k in
                          ("item_id", "name", "ean", "reference", "price_ttc", "type", "parent_item_id")}
                         if target else None)
        entry = {"prestashop": public_source, "shopcaisse": public_target,
                 "classification": classification, "methode": method, "confidence": confidence}
        if candidates:
            entry["candidats_shopcaisse"] = [str(c.get("item_id")) for c in candidates]
        mappings.append(entry)

    counts = {name: sum(m["classification"] == name for m in mappings) for name in CLASSIFICATIONS}
    document = {"generated_at": datetime.now(timezone.utc).isoformat(),
                "prestashop_total": len(prestashop), "shopcaisse_total": len(shopcaisse),
                "certaine": counts["CERTAINE"], "probable": counts["PROBABLE"],
                "ambigue": counts["AMBIGUE"], "non_trouvee": counts["NON_TROUVEE"],
                "conflit": counts["CONFLIT"], "mappings": mappings}
    quality = {k: document[k] for k in ("generated_at", "prestashop_total", "shopcaisse_total",
                                        "certaine", "probable", "ambigue", "non_trouvee", "conflit")}
    quality["details"] = {name: [m for m in mappings if m["classification"] == name]
                          for name in CLASSIFICATIONS if name != "CERTAINE"}
    return document, quality


def pull_prestashop(client: PrestaShopClient) -> list[dict[str, Any]]:
    products = list(client.iter_resource("products"))
    combinations = list(client.iter_resource("combinations"))
    stocks = list(client.iter_resource("stock_availables"))
    values = {str(v.get("id")): _text(v.get("name"))
              for v in client.iter_resource("product_option_values")}
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for combination in combinations:
        by_product[str(combination.get("id_product"))].append(combination)
    stock_by_pair = {(str(s.get("id_product")), str(s.get("id_product_attribute", "0"))): s.get("quantity")
                     for s in stocks}
    units = []
    for product in products:
        pid = product.get("id")
        rows = by_product.get(str(pid)) or [None]
        for combination in rows:
            cid = combination.get("id") if combination else None
            associations = (combination or {}).get("associations", {}).get("product_option_values", [])
            if isinstance(associations, dict):
                associations = associations.get("product_option_value", associations)
            if isinstance(associations, dict):
                associations = [associations]
            attributes = [values.get(str(v.get("id")), str(v.get("id"))) for v in associations or []]
            units.append({"key": prestashop_key(pid, cid), "product_id": pid, "combination_id": cid,
                          "name": _text(product.get("name")), "attributes": attributes,
                          "ean": _text((combination or product).get("ean13")),
                          "reference": _text((combination or {}).get("reference")) or _text(product.get("reference")),
                          "stock": stock_by_pair.get((str(pid), str(cid or 0)))})
    fields = client.pull_import_fields(units)
    for unit in units:
        field = fields.get(f"{unit['product_id']}:{unit['combination_id'] or 0}", {})
        unit["price_ttc"] = field.get("price_ttc")
    return units


def pull_shopcaisse(client: ShopCaisseClient, company_id: str) -> list[dict[str, Any]]:
    rows = client.pull_company_items(company_id)
    return [{"item_id": row.get("id"), "name": _text(row.get("name")),
             "ean": _text((row.get("barcodes") or [""])[0]) if isinstance(row.get("barcodes"), list)
             else _text(row.get("barcode", row.get("ean"))),
             "reference": _text(row.get("reference", row.get("sku"))),
             "price_ttc": row.get("defaultPrice", row.get("price")), "type": row.get("type"),
             "parent_item_id": row.get("parentItem")} for row in rows]


def run(output: Path = Path("dist"), report_paths: Iterable[Path] = ()) -> dict[str, Any]:
    required = ("PRESTASHOP_API_KEY", "SHOPCAISSE_API_KEY", "SHOPCAISSE_COMPANY_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Configuration absente: " + ", ".join(missing))
    prestashop = pull_prestashop(PrestaShopClient(
        resolve_prestashop_api_url(),
        os.environ["PRESTASHOP_API_KEY"]))
    shopcaisse = pull_shopcaisse(ShopCaisseClient(os.environ["SHOPCAISSE_API_KEY"]),
                                os.environ["SHOPCAISSE_COMPANY_ID"])
    reports = [json.loads(path.read_text()) for path in report_paths if path.is_file()]
    mapping, quality = build_mapping(prestashop, shopcaisse, reports)
    output.mkdir(parents=True, exist_ok=True)
    (output / "mapping-prestashop-shopcaisse.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    (output / "rapport-mapping-prestashop-shopcaisse.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n")
    return quality
