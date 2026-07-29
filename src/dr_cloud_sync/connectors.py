"""Barcode connector contracts. There is deliberately no live HTTP adapter yet."""
from __future__ import annotations
from typing import Protocol, Any
from .domain import Product


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
