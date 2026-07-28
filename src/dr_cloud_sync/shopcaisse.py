"""Read-only ShopCaisse catalogue client and PrestaShop reconciliation."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


API_URL = "https://api.shop-caisse.com/v1"


class ShopCaisseError(RuntimeError):
    """A sanitized API error which never contains credentials."""


class ShopCaisseClient:
    def __init__(self, api_key: str, *, timeout: float = 30, page_size: int = 25,
                 opener: Callable[..., Any] = urlopen, retries: int = 3) -> None:
        if not api_key:
            raise ShopCaisseError("SHOPCAISSE_API_KEY est absent")
        self._authorization = f"Bearer {api_key}"
        if not 1 <= page_size <= 25:
            raise ShopCaisseError("page_size ShopCaisse doit être compris entre 1 et 25")
        self.timeout, self.page_size, self.opener, self.retries = timeout, page_size, opener, retries

    def pull_products(self) -> list[dict[str, Any]]:
        """Return the real ShopCaisse items, kept for callers of the old method."""
        return self.pull_catalogue()["items"]

    def pull_company_items(self, company_id: str) -> list[dict[str, Any]]:
        """GET all items for one company (used by the pilot duplicate barrier)."""
        return self._get_paginated(f"/companies/{quote(company_id, safe='')}/items")

    def get_company_item(self, company_id: str, item_id: str) -> dict[str, Any]:
        """GET an item after creation; this method cannot perform a write."""
        payload = self._get(
            f"{API_URL}/companies/{quote(company_id, safe='')}/items/{quote(item_id, safe='')}"
        )
        if not isinstance(payload, dict):
            raise ShopCaisseError("Format de l'article ShopCaisse inattendu")
        return payload

    def create_company_item(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST the sole write endpoint authorized for the explicitly gated pilot."""
        errors = validate_create_item(payload, company_id)
        if errors:
            raise ShopCaisseError("Payload CreateSimpleItemDto invalide: " + ", ".join(errors))
        url = f"{API_URL}/companies/{quote(company_id, safe='')}/items"
        request = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
            "Authorization": self._authorization, "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ShopCaisseError(f"ShopCaisse HTTP {exc.code} sur {urlparse(url).path}") from exc
        except (URLError, TimeoutError) as exc:
            raise ShopCaisseError(f"ShopCaisse indisponible sur {urlparse(url).path}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ShopCaisseError("Réponse JSON ShopCaisse invalide") from exc
        if not isinstance(result, dict) or not result.get("id"):
            raise ShopCaisseError("Identifiant de l'article ShopCaisse créé absent")
        return result

    def pull_catalogue(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch catalogue resources declared by the ShopCaisse OpenAPI schema."""
        companies = self._get_paginated("/companies")
        stores = self._get_paginated("/stores")
        items: list[dict[str, Any]] = []
        price_lists: list[dict[str, Any]] = []
        prices: list[dict[str, Any]] = []
        stocks: list[dict[str, Any]] = []

        for company in companies:
            company_id = _identifier(company, "company")
            items.extend(self._get_paginated(f"/companies/{company_id}/items"))
            company_price_lists = self._get_paginated(f"/companies/{company_id}/prices")
            price_lists.extend(company_price_lists)
            for price_list in company_price_lists:
                price_list_id = _identifier(price_list, "price list")
                prices.extend(self._get_paginated(
                    f"/companies/{company_id}/prices/{price_list_id}"
                ))

        for store in stores:
            store_id = _identifier(store, "store")
            for stock in self._get_paginated(f"/stores/{store_id}/stocks"):
                # ``store`` is linkage metadata, not an invented catalogue value.
                stocks.append({"store": store_id, **stock})

        return {
            "companies": companies,
            "stores": stores,
            "items": items,
            "priceLists": price_lists,
            "prices": prices,
            "stocks": stocks,
        }

    def _get_paginated(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 0
        seen: set[str] = set()
        while True:
            url = f"{API_URL}{path}?{urlencode({'page': page, 'pageSize': self.page_size})}"
            if url in seen:
                raise ShopCaisseError("Pagination ShopCaisse invalide")
            seen.add(url)
            payload = self._get(url)
            batch = _rows(payload)
            rows.extend(batch)
            if not isinstance(payload, dict) or not payload.get("hasNextPage"):
                return rows
            page += 1

    def _get(self, url: str) -> Any:
        request = Request(url, method="GET", headers={
            "Authorization": self._authorization, "Accept": "application/json"
        })
        for attempt in range(self.retries):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.retries - 1:
                    raise ShopCaisseError(
                        f"ShopCaisse HTTP {exc.code} sur {urlparse(url).path}"
                    ) from exc
            except (URLError, TimeoutError) as exc:
                if attempt == self.retries - 1:
                    raise ShopCaisseError(
                        f"ShopCaisse indisponible sur {urlparse(url).path}"
                    ) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ShopCaisseError("Réponse JSON ShopCaisse invalide") from exc
            time.sleep(0.25 * 2**attempt)
        raise AssertionError("unreachable")


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = next((payload[k] for k in ("products", "articles", "data", "items", "results")
                     if isinstance(payload.get(k), list)), [])
    else:
        rows = []
    if not all(isinstance(row, dict) for row in rows):
        raise ShopCaisseError("Format du catalogue ShopCaisse inattendu")
    return rows


def _identifier(row: dict[str, Any], resource: str) -> str:
    value = row.get("id")
    if not isinstance(value, str) or not value:
        raise ShopCaisseError(f"Identifiant {resource} ShopCaisse absent")
    return value


def normalize(products: list[dict[str, Any]], *, prices: list[dict[str, Any]] | None = None,
              stocks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    prices_by_item: dict[Any, list[dict[str, Any]]] = {}
    stocks_by_item: dict[Any, list[dict[str, Any]]] = {}
    for price in prices or []:
        prices_by_item.setdefault(price.get("item"), []).append(price)
    for stock in stocks or []:
        stocks_by_item.setdefault(stock.get("item"), []).append(stock)
    by_id = {row.get("id"): row for row in products if row.get("id") is not None}
    result = []
    for row in products:
        barcodes = row.get("barcodes")
        ean = (barcodes[0] if isinstance(barcodes, list) and barcodes else
               _value(row, "ean", "ean13", "barcode", "code_barre"))
        item_prices = prices_by_item.get(row.get("id"), [])
        item_stocks = stocks_by_item.get(row.get("id"), [])
        parent = by_id.get(row.get("parentItem"), {})
        is_variation = row.get("type") == "VARIATION" or bool(row.get("parentItem"))
        own_name = _value(row, "name", "product", "nom", "label")
        parent_name = _value(parent, "name", "product", "nom", "label")
        result.append({
            "id": row.get("id"),
            "company_id": row.get("companyId"),
            "ean": str(ean).strip(),
            "sku": _value(row, "reference", "sku", "ref"),
            "product": (parent_name if is_variation and parent_name else own_name),
            "variation": (own_name if is_variation else
                          _value(row, "variation", "variant", "declinaison", "attribute")),
            "parent_item_id": row.get("parentItem"),
            "type": row.get("type"),
            "price": row.get("defaultPrice"),
            "prices": item_prices,
            "stocks": item_stocks,
            "source": row,
        })
    return result


def extract_prestashop(payload: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Extract comparable sale units from the reconstructed snapshot.

    A product with combinations is represented by its combinations; a product
    without one remains a sale unit.  This avoids both dropping the nested
    catalogue (the former bug) and counting a variation product twice.
    """
    products = payload.get("catalogue", []) if isinstance(payload, dict) else payload
    if not isinstance(products, list):
        raise ValueError("Structure du snapshot PrestaShop inattendue")
    entries: list[dict[str, Any]] = []
    combination_count = 0
    for product in products:
        combinations = product.get("declinaisons") or []
        rows = combinations or [None]
        combination_count += len(combinations)
        for combination in rows:
            attributes = combination.get("attributs", []) if combination else []
            names = [str(a.get("nom", "")).strip() for a in attributes if a.get("nom")]
            color = _attribute(attributes, ("couleur", "color"))
            size = _attribute(attributes, ("taille", "size"))
            entries.append({
                "product_id": product.get("id"),
                "combination_id": combination.get("id") if combination else None,
                "product_name": _value(product, "nom", "name"),
                "variation_name": " / ".join(names),
                "attributes": attributes,
                "color": color,
                "size": size,
                "ean": _value(combination or product, "ean", "ean13"),
                "reference": _value(combination or product, "reference", "sku"),
                "price": (combination or product).get("price"),
                "stock": combination.get("stock") if combination else product.get("stock"),
                "source": combination or product,
                # Compatibility aliases for existing report consumers.
                "id": combination.get("id") if combination else product.get("id"),
                "product": _value(product, "nom", "name"),
                "variation": " / ".join(names),
                "sku": _value(combination or product, "reference", "sku"),
            })
    return entries, {"products": len(products), "combinations": combination_count,
                     "comparable_entries": len(entries)}


WRITE_ENDPOINTS = [
    {"method": "POST", "path": "/v1/companies/{company}/items",
     "schema": "CreateSimpleItemDto", "required": ["name", "price"],
     "purpose": "créer un article simple (nom, référence, code-barres et prix)"},
    {"method": "PUT", "path": "/v1/companies/{company}/items/{item}",
     "schema": "ItemEditDto", "required": [],
     "purpose": "modifier nom, libellé, descriptions ou référence"},
    {"method": "POST", "path": "/v1/companies/{company}/prices",
     "schema": "CreatePriceListDto", "required": ["store", "name", "prices"],
     "purpose": "créer une liste de prix"},
    {"method": "PUT", "path": "/v1/companies/{company}/prices/{priceList}",
     "schema": "EditPriceDto[]", "required": ["price", "itemId"],
     "purpose": "définir plusieurs prix"},
    {"method": "PUT", "path": "/v1/companies/{company}/prices/{priceList}/items/{item}",
     "schema": "PriceDto", "required": ["price"], "purpose": "définir un prix"},
    {"method": "DELETE", "path": "/v1/companies/{company}/items/{item}",
     "schema": None, "required": [], "purpose": "supprimer un article"},
]

CREATE_ITEM_SCHEMA = {
    "schema": "CreateSimpleItemDto",
    "type": "object",
    "required": ["name", "price"],
    "properties": {
        "name": {"type": "string"}, "textLines": {"type": "array", "items": "string", "length": 2},
        "reference": {"type": "string"}, "barcode": {"type": "string"},
        "price": {"type": "number", "description": "prix TTC"},
        "description": {"type": "string"}, "familyId": {"type": "string"},
        "vatOnSite": {"type": "number"}, "vatTakeAway": {"type": "number"},
    },
}


def validate_create_item(payload: dict[str, Any], company_id: str | None) -> list[str]:
    """Validate the locally prepared request against CreateSimpleItemDto."""
    errors = []
    if not company_id:
        errors.append("companyId (paramètre de chemin)")
    if not isinstance(payload.get("name"), str) or not payload["name"].strip():
        errors.append("name")
    price = payload.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        errors.append("price")
    for field in ("reference", "barcode"):
        if field in payload and not isinstance(payload[field], str):
            errors.append(field)
    return errors


def build_import_dry_run(raw: dict[str, Any], presta_payload: Any,
                         import_fields: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build the three import simulations without owning any network writer."""
    shop = normalize(raw["items"], prices=raw["prices"], stocks=raw["stocks"])
    presta, counts = extract_prestashop(presta_payload)
    claimed: set[Any] = set()
    plan = []
    category_counts = {k: 0 for k in ("PRET_A_CREER", "EXISTANTE_CERTAINE", "EXISTANTE_PROBABLE", "AMBIGUE", "CONFLIT", "DONNEES_MANQUANTES")}
    company_ids = [c.get("id") for c in raw.get("companies", []) if c.get("id")]
    company_id = company_ids[0] if len(company_ids) == 1 else None
    duplicate_notes = []
    for entry in presta:
        ranked = sorted(((_score(s, entry), s) for s in shop), key=lambda x: x[0][0], reverse=True)
        credible = [(score, method, s) for (score, method), s in ranked if score >= .78]
        conflicts = []
        match = None
        score = 0.0
        reason = "aucune correspondance ShopCaisse"
        if credible:
            score, method, match = credible[0]
            peers = [x for x in credible if score - x[0] <= .025]
            if len(peers) > 1:
                category = "AMBIGUE"
                conflicts = [{"shopcaisse_id": x[2].get("id"), "score": x[0], "raison": x[1]} for x in peers]
                reason = "plusieurs articles ShopCaisse ont un score équivalent"
                match = None
            elif score >= .99:
                category, reason = "EXISTANTE_CERTAINE", method
            else:
                category = "EXISTANTE_PROBABLE" if score >= .90 else "AMBIGUE"
                reason = method
        else:
            category = "CREATION_CANDIDATE"
        if match and match.get("id") in claimed:
            category = "CONFLIT"
            conflicts.append({"shopcaisse_id": match.get("id"), "raison": "déjà rattaché à une autre unité PrestaShop"})
        if match:
            claimed.add(match.get("id"))
        if conflicts:
            duplicate_notes.append({"product_id": entry["product_id"], "combination_id": entry["combination_id"], "conflits": conflicts})
        details = (import_fields or {}).get(f'{entry["product_id"]}:{entry["combination_id"] or 0}', {})
        if details:
            entry["reference"] = details.get("reference") or ""
            entry["sku"] = entry["reference"]
            entry["price"] = details.get("price_ttc")
        label = entry["variation_name"]
        item_name = entry["product_name"] + (f" - {label}" if label else "")
        create_fields = {"name": item_name, "price": entry["price"]}
        if entry["reference"]:
            create_fields["reference"] = entry["reference"]
        if entry["ean"]:
            create_fields["barcode"] = entry["ean"]
        missing = validate_create_item(create_fields, company_id)
        if category == "CREATION_CANDIDATE":
            category = "DONNEES_MANQUANTES" if missing else "PRET_A_CREER"
        category_counts[category] += 1
        plan.append({
            "product_id": entry["product_id"], "combination_id": entry["combination_id"],
            "nom": entry["product_name"], "declinaison": label, "attributs": entry["attributes"],
            "couleur": entry["color"], "taille": entry["size"], "EAN": entry["ean"] or None,
            "reference": entry["reference"] or None, "prix": entry["price"],
            "prix_ht": details.get("price_ht"), "prix_ttc": details.get("price_ttc", entry["price"]),
            "prix_produit_ht": details.get("product_price_ht"),
            "impact_prix_declinaison_ht": details.get("combination_price_impact_ht"),
            "devise": details.get("currency"), "source_prix": details.get("price_source"),
            "stock": entry["stock"],
            "action_prevue": category, "raison": reason,
            "shopcaisse_id": match.get("id") if match else None, "score_confiance": round(score, 3),
            "strategie_attributs": ("ARTICLE_SIMPLE_PAR_DECLINAISON" if entry["combination_id"] else "ARTICLE_SIMPLE"),
            "attributs_traduits": bool(entry["combination_id"] and label),
            "champs_disponibles": [k for k in ("name", "reference", "barcode", "price") if k in create_fields],
            "champs_calculables_sans_invention": ["name"] + (["price"] if entry["price"] is not None else []),
            "champs_reellement_manquants": missing,
            "champs_qui_seraient_crees": create_fields if category in {"PRET_A_CREER", "DONNEES_MANQUANTES"} else {},
            "champs_qui_seraient_modifies": {}, "conflits": conflicts,
            "champs_obligatoires_manquants": missing if category == "DONNEES_MANQUANTES" else [],
        })
    unmatched = [{"shopcaisse_id": s.get("id"), "nom": s.get("product"),
                  "classification": "SHOPCAISSE_EXISTANT_NON_RATTACHE"}
                 for s in shop if s.get("id") not in claimed]
    operations = []
    for row in plan:
        if row["action_prevue"] in {"PRET_A_CREER", "DONNEES_MANQUANTES"}:
            payload = row["champs_qui_seraient_crees"]
            errors = validate_create_item(payload, company_id)
            operations.append({"method": "POST", "endpoint": (f"/v1/companies/{company_id}/items" if company_id else "/v1/companies/{company}/items"),
                               "companyId": company_id, "schema": "CreateSimpleItemDto",
                               "payload": payload if not errors else None,
                               "statut": "PRET" if not errors else "BLOQUE",
                               "champs_obligatoires_manquants": row["champs_obligatoires_manquants"],
                               "validation_locale": {"valide": not errors, "erreurs": errors},
                               "product_id": row["product_id"], "combination_id": row["combination_id"]})
    anomalies = {
        "sans EAN": sum(not p["EAN"] for p in plan), "sans référence": sum(not p["reference"] for p in plan),
        "sans prix": sum(p["prix"] is None for p in plan), "sans stock": sum(p["stock"] is None for p in plan),
        "attributs impossibles à traduire": sum(bool(p["combination_id"]) and not p["attributs_traduits"] for p in plan),
        "champs obligatoires ShopCaisse manquants": sum(bool(p["champs_obligatoires_manquants"]) for p in plan),
        "doublons potentiels": duplicate_notes,
    }
    report = {"produits PrestaShop": counts["products"], "déclinaisons PrestaShop": counts["combinations"],
              "unités de vente PrestaShop": counts["comparable_entries"], "articles ShopCaisse actuels": len(shop),
              "existantes certaines": category_counts["EXISTANTE_CERTAINE"],
              "existantes probables": category_counts["EXISTANTE_PROBABLE"], "ambiguës": category_counts["AMBIGUE"],
              "prêts à créer": category_counts["PRET_A_CREER"], "conflits": category_counts["CONFLIT"],
              "données manquantes": category_counts["DONNEES_MANQUANTES"],
              "ShopCaisse existants non rattachés": len(unmatched),
              "avec prix valide": sum(isinstance(p["prix"], (int, float)) for p in plan),
              "sans prix": sum(p["prix"] is None for p in plan),
              "avec référence": sum(bool(p["reference"]) for p in plan), "sans référence": sum(not p["reference"] for p in plan),
              "avec EAN": sum(bool(p["EAN"]) for p in plan), "sans EAN": sum(not p["EAN"] for p in plan),
              "avec stock": sum(p["stock"] is not None for p in plan), "sans stock": sum(p["stock"] is None for p in plan),
              "attributs correctement traduits": sum(p["attributs_traduits"] for p in plan),
              "attributs réellement impossibles à traduire": anomalies["attributs impossibles à traduire"],
              "anomalies": anomalies}
    plan_doc = {"mode": "DRY_RUN_SANS_ECRITURE", "entrees": plan, "shopcaisse_existants_non_rattaches": unmatched}
    dry_doc = {"mode": "DRY_RUN_SANS_ENVOI", "openapi": {"source": "https://api.shop-caisse.com/v1/docs-json",
               "endpoints_ecriture_identifies": WRITE_ENDPOINTS,
               "schema_creation_article": CREATE_ITEM_SCHEMA,
               "identifiants": {"companyId": company_id, "storeIds": [s.get("id") for s in raw.get("stores", []) if s.get("id")]},
               "limitations": ["aucun endpoint d'écriture du stock", "aucun schéma de création de variante ou relation parent/enfant; un article simple déterministe est préparé par déclinaison"]},
               "operations": operations}
    return plan_doc, dry_doc, report


def run_import_dry_run(api_key: str, presta_path: Path, dist: Path, *,
                       prestashop_client: Any | None = None) -> dict[str, Any]:
    """GET the catalogue and write local simulation files only."""
    raw = ShopCaisseClient(api_key).pull_catalogue()
    payload = json.loads(presta_path.read_text(encoding="utf-8"))
    units, _ = extract_prestashop(payload)
    import_fields = prestashop_client.pull_import_fields(units) if prestashop_client else None
    if import_fields:
        payload["import_fields"] = import_fields
        presta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        import_fields = payload.get("import_fields") if isinstance(payload, dict) else None
    documents = build_import_dry_run(raw, payload, import_fields)
    dist.mkdir(parents=True, exist_ok=True)
    names = ("plan-import-prestashop-shopcaisse.json", "dry-run-import-shopcaisse.json", "rapport-dry-run-import.json")
    for name, document in zip(names, documents):
        (dist / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return documents[2]


def _attribute(attributes: list[dict[str, Any]], groups: tuple[str, ...]) -> str:
    for attribute in attributes:
        group = _text(str(attribute.get("groupe", "")))
        if any(name in group for name in groups):
            return str(attribute.get("nom", "")).strip()
    return ""


def reconcile(shop: list[dict[str, Any]], presta_payload: Any) -> dict[str, Any]:
    presta, counts = extract_prestashop(presta_payload)
    groups: dict[str, list[Any]] = {k: [] for k in
                                    ("certaines", "probables", "possibles", "ambigues")}
    used: set[int] = set()
    unmatched_shop: list[dict[str, Any]] = []
    for s in shop:
        candidates = sorted(((*_score(s, p), i, p) for i, p in enumerate(presta)),
                            reverse=True, key=lambda item: item[0])
        credible = [candidate for candidate in candidates if candidate[0] >= .78]
        if not credible:
            unmatched_shop.append(s)
            continue
        best_score, method, index, p = credible[0]
        # More than one near-equivalent candidate is useful information, not an
        # arbitrary match. Exact identifiers remain decisive.
        plausible = [c for c in credible if best_score - c[0] <= .025]
        if len(plausible) > 1:
            groups["ambigues"].append({
                "shopcaisse": s,
                "candidats": [_match(s, c[3], c[0], c[1]) for c in plausible],
                "score": best_score,
                "raison": "plusieurs candidats PrestaShop de scores équivalents",
            })
            used.update(c[2] for c in plausible)
            continue
        bucket = "certaines" if best_score >= .99 else ("probables" if best_score >= .90 else "possibles")
        groups[bucket].append(_match(s, p, best_score, method))
        used.add(index)
    report = {**groups,
              "uniquement_shopcaisse": unmatched_shop,
              "uniquement_prestashop": [p for i, p in enumerate(presta) if i not in used]}
    report["statistiques"] = {
        "articles_shopcaisse": len(shop), **counts,
        **{key: len(report[key]) for key in groups},
        "uniquement_shopcaisse": len(unmatched_shop),
        "uniquement_prestashop": len(report["uniquement_prestashop"]),
    }
    assert sum(len(report[k]) for k in (*groups, "uniquement_shopcaisse")) == len(shop)
    return report


def _match(shop: dict[str, Any], presta: dict[str, Any], score: float, method: str) -> dict[str, Any]:
    return {"shopcaisse": shop, "prestashop": presta, "methode": method,
            "raison": method.replace("_", " "), "score": round(score, 3)}


def _value(row: dict[str, Any], *keys: str) -> str:
    return str(next((row[k] for k in keys if row.get(k) not in (None, "")), "")).strip()


def _text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _score(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, str]:
    ean_a, ean_b = str(a.get("ean") or "").strip(), str(b.get("ean") or "").strip()
    if ean_a and ean_a == ean_b: return (1.0, "ean_exact")
    ref_a, ref_b = _text(str(a.get("sku") or "")), _text(str(b.get("reference") or ""))
    if ref_a and len(ref_a) >= 3 and ref_a == ref_b: return (0.995, "reference_exacte")
    product_a, product_b = _text(str(a.get("product") or "")), _text(str(b.get("product_name") or ""))
    variation_a, variation_b = _tokens(a.get("variation")), _tokens(b.get("variation_name"))
    if product_a and product_a == product_b and variation_a and variation_a == variation_b:
        typed = b.get("color") or b.get("size")
        return (.97, "produit_couleur_taille_identiques" if typed else
                "produit_declinaison_identiques")
    if product_a and product_a == product_b and not variation_a and not variation_b:
        return (.97, "produit_declinaison_identiques")
    joined_a, joined_b = " ".join((product_a, " ".join(variation_a))), " ".join((product_b, " ".join(variation_b)))
    ratio = SequenceMatcher(None, joined_a, joined_b).ratio() if joined_a and joined_b else 0
    # A typed colour or size contradiction vetoes fuzzy text matching.
    for key in ("color", "size"):
        expected = _text(str(b.get(key) or ""))
        if expected and variation_a and expected not in " ".join(variation_a) and ratio < .94:
            return (0, "attribut_contradictoire")
    return (round(ratio, 3), "texte_et_attributs_compatibles") if ratio >= .78 else (0, "aucune")


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(sorted(filter(None, _text(str(value or "")).split())))


def pull_and_write(api_key: str, presta_path: Path, dist: Path) -> dict[str, int]:
    raw = ShopCaisseClient(api_key).pull_catalogue()
    normalized = normalize(raw["items"], prices=raw["prices"], stocks=raw["stocks"])
    if not presta_path.is_file():
        raise ShopCaisseError(f"Snapshot PrestaShop introuvable: {presta_path}")
    report = reconcile(normalized, json.loads(presta_path.read_text(encoding="utf-8")))
    dist.mkdir(parents=True, exist_ok=True)
    for name, value in (("catalogue-shopcaisse-brut.json", raw),
                        ("catalogue-shopcaisse-normalise.json", normalized),
                        ("rapport-shopcaisse-prestashop.json", report)):
        (dist / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"produits": len(raw["items"])}
