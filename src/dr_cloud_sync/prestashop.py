"""Small, read-only client for the PrestaShop Webservice JSON API."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class PrestaShopError(RuntimeError):
    """A sanitized Webservice error (credentials are never included)."""


OpenUrl = Callable[..., Any]


class PrestaShopClient:
    RESOURCES = (
        "products",
        "combinations",
        "product_options",
        "product_option_values",
        "images",
        "stock_availables",
        "manufacturers",
        "suppliers",
    )

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        timeout: float = 30,
        page_size: int = 100,
        opener: OpenUrl = urlopen,
        retries: int = 3,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._authorization = "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()
        self.timeout = timeout
        self.page_size = page_size
        self.opener = opener
        self.retries = retries

    def iter_resource(self, resource: str) -> Iterator[dict[str, Any]]:
        if resource not in self.RESOURCES:
            raise ValueError(f"Ressource non autorisée: {resource}")
        offset = 0
        while True:
            payload = self._get(resource, {"display": "full", "limit": f"{offset},{self.page_size}"})
            rows = self._extract_rows(payload, resource)
            yield from rows
            if len(rows) < self.page_size:
                break
            offset += self.page_size

    @staticmethod
    def _extract_rows(payload: dict[str, Any], resource: str) -> list[dict[str, Any]]:
        container = payload.get(resource, [])
        if isinstance(container, dict):
            singular = {
                "products": "product",
                "combinations": "combination",
                "product_options": "product_option",
                "product_option_values": "product_option_value",
                "images": "image",
                "stock_availables": "stock_available",
                "manufacturers": "manufacturer",
                "suppliers": "supplier",
            }[resource]
            container = container.get(singular, container)
        if not container:
            return []
        if isinstance(container, dict):
            container = [container]
        if not isinstance(container, list) or not all(isinstance(row, dict) for row in container):
            raise PrestaShopError(f"Réponse inattendue pour {resource}")
        return container

    def _get(self, resource: str, query: dict[str, str]) -> dict[str, Any]:
        url = f"{self.api_url}/{resource}?{urlencode({**query, 'output_format': 'JSON'})}"
        request = Request(url, headers={"Authorization": self._authorization, "Accept": "application/json"})
        for attempt in range(self.retries):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
                    if not isinstance(decoded, dict):
                        raise PrestaShopError(f"Réponse inattendue pour {resource}")
                    return decoded
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.retries - 1:
                    raise PrestaShopError(f"PrestaShop HTTP {exc.code} sur {resource}") from exc
            except (URLError, TimeoutError) as exc:
                if attempt == self.retries - 1:
                    raise PrestaShopError(f"PrestaShop indisponible sur {resource}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PrestaShopError(f"JSON invalide pour {resource}") from exc
            time.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable")

    def pull_import_fields(self, sale_units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Read authoritative SKU and computed prices for snapshot sale units.

        PrestaShop's stored product and combination prices are tax-exclusive.  Its
        documented ``price[...]`` Webservice query asks the shop itself to compute
        the final price, including its tax rules and combination impact.  Keeping
        this method on the read-only client also makes the GET-only boundary
        explicit and testable.
        """
        products = {str(row["id"]): row for row in self.iter_resource("products")}
        combinations = {str(row["id"]): row for row in self.iter_resource("combinations")}
        result: dict[str, dict[str, Any]] = {}
        for unit in sale_units:
            product_id = str(unit["product_id"])
            combination_id = unit.get("combination_id")
            product = products.get(product_id, {})
            combination = combinations.get(str(combination_id), {}) if combination_id is not None else {}
            query = {
                "display": "[id,price,final_price]",
                "filter[id]": product_id,
                "price[final_price][use_tax]": "1",
                "price[final_price_ht][use_tax]": "0",
            }
            if combination_id is not None:
                query["price[final_price][product_attribute]"] = str(combination_id)
                query["price[final_price_ht][product_attribute]"] = str(combination_id)
            payload = self._get("products", query)
            rows = self._extract_rows(payload, "products")
            computed = rows[0] if rows else {}
            key = f"{product_id}:{combination_id or 0}"
            product_price_ht = _decimal(product.get("price"))
            impact_ht = _decimal(combination.get("price"))
            final_ht = (round(product_price_ht + (impact_ht or 0), 6)
                        if product_price_ht is not None else None)
            result[key] = {
                "reference": _first(combination, "reference", "supplier_reference")
                or _first(product, "reference", "supplier_reference"),
                "product_reference": _first(product, "reference", "supplier_reference"),
                "combination_reference": _first(combination, "reference", "supplier_reference"),
                "price_ht": final_ht,
                "price_ttc": _decimal(computed.get("final_price")),
                "product_price_ht": product_price_ht,
                "combination_price_impact_ht": impact_ht,
                "currency": None,
                "price_source": "PrestaShop GET products price[final_price]",
            }
        return result


def _first(row: dict[str, Any], *keys: str) -> str | None:
    return next((str(row[key]).strip() for key in keys if row.get(key) not in (None, "")), None)


def _decimal(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
