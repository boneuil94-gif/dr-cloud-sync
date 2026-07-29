"""Barcode connector contracts. There is deliberately no live HTTP adapter yet."""
from __future__ import annotations
from typing import Protocol, Any
from .domain import Product
from .os_config import env_bool


class SafeModeViolation(RuntimeError):
    """Raised before any mutable request can leave the process."""


def assert_external_write_allowed(system: str, method: str) -> None:
    """Global fail-closed guard shared by external connector write paths."""
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and env_bool("DRCLOUD_SAFE_MODE", True):
        import logging
        logging.getLogger("drcloud.audit").warning(
            "external_write_blocked system=%s method=%s", system, method.upper()
        )
        raise SafeModeViolation(f"DRCLOUD_SAFE_MODE bloque {method.upper()} vers {system}")


def prestashop_barcode_target(product: Product, ean: str) -> dict[str, Any]:
    """Build the resource target supported by PrestaShop's product/combination API."""
    combination = product.combination_id not in (None, "", 0, "0")
    return {"resource": "combinations" if combination else "products",
            "id": product.combination_id if combination else product.product_id, "ean13": ean}


def shopcaisse_barcode_target(product: Product, ean: str) -> dict[str, Any]:
    """Only identify the mapped item; no unverified ShopCaisse endpoint is invented."""
    return {"shopcaisse_item_id": product.shopcaisse_item_id, "ean": ean}


class BarcodeConnector(Protocol):
    def write_and_verify(self, product: Product, ean: str) -> None: ...


class DisabledConnector:
    """Safe default used until each write API has been explicitly validated."""
    def write_and_verify(self, product: Product, ean: str) -> None:
        raise RuntimeError("live barcode connector disabled")
