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
        "stock_availables",
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
                "stock_availables": "stock_available",
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

