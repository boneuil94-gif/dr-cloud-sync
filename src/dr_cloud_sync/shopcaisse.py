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
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


API_URL = "https://api.shop-caisse.com/v1"


class ShopCaisseError(RuntimeError):
    """A sanitized API error which never contains credentials."""


class ShopCaisseClient:
    def __init__(self, api_key: str, *, timeout: float = 30, page_size: int = 100,
                 opener: Callable[..., Any] = urlopen, retries: int = 3) -> None:
        if not api_key:
            raise ShopCaisseError("SHOPCAISSE_API_KEY est absent")
        self._authorization = f"Bearer {api_key}"
        self.timeout, self.page_size, self.opener, self.retries = timeout, page_size, opener, retries

    def pull_products(self) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        url: str | None = f"{API_URL}/products?{urlencode({'page': 1, 'limit': self.page_size})}"
        seen: set[str] = set()
        while url:
            if url in seen or not url.startswith(API_URL + "/"):
                raise ShopCaisseError("Pagination ShopCaisse invalide")
            seen.add(url)
            payload = self._get(url)
            rows = _rows(payload)
            products.extend(rows)
            url = _next_url(payload, url, len(rows), self.page_size)
        return products

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
                    raise ShopCaisseError(f"ShopCaisse HTTP {exc.code} sur products") from exc
            except (URLError, TimeoutError) as exc:
                if attempt == self.retries - 1:
                    raise ShopCaisseError("ShopCaisse indisponible sur products") from exc
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


def _next_url(payload: Any, current: str, count: int, page_size: int) -> str | None:
    if isinstance(payload, dict):
        candidate = payload.get("next") or (payload.get("links") or {}).get("next")
        if candidate:
            return str(candidate)
        meta = payload.get("meta") or payload.get("pagination") or {}
        page = int(meta.get("current_page", meta.get("page", 0)) or 0)
        last = int(meta.get("last_page", meta.get("total_pages", 0)) or 0)
        if page and last and page < last:
            query = parse_qs(urlparse(current).query)
            query["page"] = [str(page + 1)]
            return f"{API_URL}/products?{urlencode(query, doseq=True)}"
    if count == page_size:
        query = parse_qs(urlparse(current).query)
        query["page"] = [str(int(query.get('page', ['1'])[0]) + 1)]
        return f"{API_URL}/products?{urlencode(query, doseq=True)}"
    return None


def normalize(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in products:
        result.append({
            "id": row.get("id"),
            "ean": _value(row, "ean", "ean13", "barcode", "code_barre"),
            "sku": _value(row, "sku", "reference", "ref"),
            "product": _value(row, "product", "name", "nom", "label"),
            "variation": _value(row, "variation", "variant", "declinaison", "attribute"),
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
    raw = ShopCaisseClient(api_key).pull_products()
    normalized = normalize(raw)
    if not presta_path.is_file():
        raise ShopCaisseError(f"Snapshot PrestaShop introuvable: {presta_path}")
    report = reconcile(normalized, json.loads(presta_path.read_text(encoding="utf-8")))
    dist.mkdir(parents=True, exist_ok=True)
    for name, value in (("catalogue-shopcaisse-brut.json", raw),
                        ("catalogue-shopcaisse-normalise.json", normalized),
                        ("rapport-shopcaisse-prestashop.json", report)):
        (dist / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"produits": len(raw)}
