import sqlite3

from dr_cloud_sync.replenishment import ReplenishmentEngine


def _minimum_stock_schema(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE stock_movements(drcloud_product_key TEXT, quantity_delta INTEGER, movement_type TEXT, applied_at TEXT, status TEXT);
        CREATE TABLE sale_events(product_key TEXT, sold_at TEXT, quantity INTEGER, line_total_ht TEXT, cost_basis TEXT, event_kind TEXT);
        CREATE TABLE purchase_orders(purchase_order_id TEXT, supplier_id TEXT, ordered_at TEXT, created_at TEXT, status TEXT);
        CREATE TABLE purchase_order_lines(line_id TEXT, purchase_order_id TEXT, product_key TEXT, ordered_quantity INTEGER, unit_cost TEXT);
        CREATE TABLE goods_receipts(receipt_id TEXT, purchase_order_id TEXT, received_at TEXT, status TEXT);
        CREATE TABLE goods_receipt_lines(receipt_id TEXT, purchase_order_line_id TEXT, received_quantity INTEGER);
        CREATE TABLE suppliers(supplier_id TEXT, status TEXT);
        """)


def test_evidence_does_not_invent_stock_refresh_timestamp(tmp_path):
    path = tmp_path / "stock.db"
    _minimum_stock_schema(path)
    engine = ReplenishmentEngine(path)

    first = engine.evidence()
    second = engine.evidence()

    assert first["last_stock_refresh_at"] is None
    assert second["last_stock_refresh_at"] is None


def test_refresh_persists_real_evidence_timestamp(tmp_path):
    path = tmp_path / "stock.db"
    _minimum_stock_schema(path)
    engine = ReplenishmentEngine(path)

    engine.refresh()
    first = engine.evidence()["last_stock_refresh_at"]
    second = engine.evidence()["last_stock_refresh_at"]

    assert first is not None
    assert second == first
