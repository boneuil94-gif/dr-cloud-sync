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
from urllib.parse import urlencode, urlparse
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
