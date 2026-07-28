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
    result = []
    for row in products:
        barcodes = row.get("barcodes")
        ean = (barcodes[0] if isinstance(barcodes, list) and barcodes else
               _value(row, "ean", "ean13", "barcode", "code_barre"))
        item_prices = prices_by_item.get(row.get("id"), [])
        item_stocks = stocks_by_item.get(row.get("id"), [])
        result.append({
            "id": row.get("id"),
            "company_id": row.get("companyId"),
            "ean": str(ean).strip(),
            "sku": _value(row, "reference", "sku", "ref"),
            "product": _value(row, "name", "product", "nom", "label"),
            "variation": (_value(row, "name") if row.get("type") == "VARIATION" else
                          _value(row, "variation", "variant", "declinaison", "attribute")),
            "parent_item_id": row.get("parentItem"),
            "type": row.get("type"),
            "price": row.get("defaultPrice"),
            "prices": item_prices,
            "stocks": item_stocks,
            "source": row,
        })
    return result


def reconcile(shop: list[dict[str, Any]], presta_payload: Any) -> dict[str, Any]:
    presta = normalize(_rows(presta_payload)) if not isinstance(presta_payload, list) else normalize(presta_payload)
    groups: dict[str, list[Any]] = {k: [] for k in ("certaines", "probables", "ambigues")}
    used: set[int] = set()
    unmatched_shop = []
    for s in shop:
        scored = sorted(((_score(s, p), i, p) for i, p in enumerate(presta) if i not in used), reverse=True,
                        key=lambda item: item[0])
        if not scored or scored[0][0][0] == 0:
            unmatched_shop.append(s); continue
        score, index, p = scored[0]
        tied = len(scored) > 1 and scored[1][0] == score
        bucket = "ambigues" if tied else ("certaines" if score[0] >= 3 else "probables")
        groups[bucket].append({"shopcaisse": s, "prestashop": p, "methode": score[1], "score": score[0]})
        if not tied:
            used.add(index)
    return {**groups, "uniquement_prestashop": [p for i, p in enumerate(presta) if i not in used],
            "uniquement_shopcaisse": unmatched_shop}


def _value(row: dict[str, Any], *keys: str) -> str:
    return str(next((row[k] for k in keys if row.get(k) not in (None, "")), "")).strip()


def _text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _score(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, str]:
    if a["ean"] and a["ean"] == b["ean"]: return (4, "ean_exact")
    if a["sku"] and a["sku"] == b["sku"]: return (3, "sku_exact")
    names, variants = (_text(a["product"]), _text(b["product"])), (_text(a["variation"]), _text(b["variation"]))
    if names[0] and names[0] == names[1] and variants[0] == variants[1]: return (3, "produit_declinaison_exacts")
    ratio = SequenceMatcher(None, " ".join((names[0], variants[0])), " ".join((names[1], variants[1]))).ratio()
    return (round(ratio, 3), "texte_fiable") if ratio >= .9 else (0, "aucune")


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
