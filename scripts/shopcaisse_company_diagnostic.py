#!/usr/bin/env python3
"""Read-only ShopCaisse company/store diagnostic used by GitHub Actions."""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.shop-caisse.com/v1"
CURRENT_ID = "91f6e7c8-0904-4983-9b3c-6807eeacc14e"


def request_get(path: str, token: str) -> tuple[int | None, Any, str | None]:
    """Perform the sole permitted HTTP operation and retain the real status."""
    method = "GET"
    if method != "GET":
        raise RuntimeError("Méthode HTTP interdite")
    request = Request(f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"}, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, parse_json(raw), None
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        body = parse_json(raw)
        return exc.code, body, safe_error(body)
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            category = "DNS"
        elif isinstance(reason, ConnectionRefusedError):
            category = "connexion refusée"
        elif isinstance(reason, ssl.SSLError):
            category = "TLS/SSL"
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            category = "timeout"
        else:
            category = "erreur réseau"
        return None, None, f"{category}: {reason}"
    except (TimeoutError, socket.timeout) as exc:
        return None, None, f"timeout: {exc}"


def parse_json(raw: str) -> Any:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def safe_error(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in ("message", "error", "detail"):
        value = body.get(key)
        if isinstance(value, str):
            return value[:500]
    return None


def records(body: Any, likely_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Locate a returned collection without assuming its response envelope."""
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        for key in likely_keys + ("data", "results", "items"):
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = records(value, likely_keys)
                if nested:
                    return nested
    return []


def field(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def main() -> int:
    token = os.environ.get("SHOPCAISSE_API_KEY", "")
    if not token:
        print("SHOPCAISSE_API_KEY absent", file=sys.stderr)
        return 2

    companies_http, companies_body, companies_error = request_get("/companies", token)
    stores_http, stores_body, stores_error = request_get("/stores", token)
    company_rows = records(companies_body, ("companies",))
    store_rows = records(stores_body, ("stores",))

    companies = []
    for row in company_rows:
        company_id = field(row, "id", "companyId", "company_id", "uuid")
        name = field(row, "name", "nom", "label")
        item_http, item_body, item_error = request_get(f"/companies/{company_id}/items", token)
        item_rows = records(item_body, ("items", "products")) if item_http == 200 else []
        companies.append({"id": company_id, "nom": name, "http_items": item_http,
                          "nombre_items": len(item_rows) if item_http == 200 else None,
                          "erreur_items": item_error})

    stores = [{"id": field(row, "id", "storeId", "store_id", "uuid"),
               "nom": field(row, "name", "nom", "label"),
               "companyId": field(row, "companyId", "company_id")} for row in store_rows]
    company_ids = {str(row["id"]) for row in companies}
    store_ids = {str(row["id"]) for row in stores}
    detected = "company" if CURRENT_ID in company_ids else "store" if CURRENT_ID in store_ids else "aucune ressource retournée"
    current_http, _, current_error = request_get(f"/companies/{CURRENT_ID}/items", token)
    recommended = next((row for row in companies if row["http_items"] == 200), None)
    report = {
        "companies": companies,
        "stores": stores,
        "id_actuel": {"id": CURRENT_ID, "type_detecte": detected,
                       "http_items": current_http, "erreur": current_error},
        "company_recommandee": ({"id": recommended["id"], "nom": recommended["nom"]}
                                 if recommended else {"id": None, "nom": None}),
        "diagnostic": {"http_companies": companies_http, "erreur_companies": companies_error,
                       "http_stores": stores_http, "erreur_stores": stores_error},
    }
    output = Path("dist/diagnostic-company-shopcaisse.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if companies_http is not None and stores_http is not None and current_http is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
