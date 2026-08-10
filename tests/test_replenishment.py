from datetime import datetime, timedelta, timezone
import sqlite3

from dr_cloud_sync.replenishment import ReplenishmentEngine


def database(path):
    db=sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE stock_movements(id TEXT,drcloud_product_key TEXT,quantity_delta INTEGER,movement_type TEXT,status TEXT,applied_at TEXT);
    CREATE TABLE sale_events(product_key TEXT,event_kind TEXT,sold_at TEXT,quantity TEXT,line_total_ht TEXT,cost_basis TEXT);
    CREATE TABLE suppliers(supplier_id TEXT,status TEXT);
    CREATE TABLE purchase_orders(purchase_order_id TEXT,supplier_id TEXT,status TEXT,ordered_at TEXT,created_at TEXT);
    CREATE TABLE purchase_order_lines(line_id TEXT,purchase_order_id TEXT,product_key TEXT,ordered_quantity INTEGER,unit_cost TEXT);
    CREATE TABLE goods_receipts(receipt_id TEXT,purchase_order_id TEXT,status TEXT,received_at TEXT);
    CREATE TABLE goods_receipt_lines(receipt_id TEXT,purchase_order_line_id TEXT,received_quantity INTEGER);
    """)
    return db


def test_projection_uses_only_observed_lead_cost_and_stock(tmp_path):
    path=tmp_path/"os.db"; db=database(path); now=datetime.now(timezone.utc)
    db.execute("INSERT INTO suppliers VALUES('sup:1','ACTIVE')")
    db.execute("INSERT INTO purchase_orders VALUES('po:1','sup:1','RECEIVED',?,?)",((now-timedelta(days=10)).isoformat(),(now-timedelta(days=11)).isoformat()))
    db.execute("INSERT INTO purchase_order_lines VALUES('line:1','po:1','drc:1',20,'2.50')")
    db.execute("INSERT INTO goods_receipts VALUES('gr:1','po:1','APPLIED',?)",(now.isoformat(),))
    db.execute("INSERT INTO goods_receipt_lines VALUES('gr:1','line:1',20)")
    db.execute("INSERT INTO stock_movements VALUES('m1','drc:1',2,'SUPPLIER_RECEIPT','APPLIED',?)",(now.isoformat(),))
    for day in range(7): db.execute("INSERT INTO sale_events VALUES('drc:1','SALE',?,'2','20','5')",((now-timedelta(days=day)).isoformat(),))
    db.commit(); engine=ReplenishmentEngine(path)
    row=engine.snapshot("drc:1",at=now)
    assert row["observed_lead_days"]==10 and row["suggested_quantity"]==32
    assert row["estimated_cost"]=="80.00" and row["confidence"]=="COMPLETE"
    result=engine.refresh(); assert result["suggestions_generated"]==1
    assert engine.refresh()["suggestions_generated"]==0  # one active proposal per product


def test_unknowns_remain_unknown_and_partial(tmp_path):
    path=tmp_path/"legacy.db"; db=database(path); now=datetime.now(timezone.utc)
    db.execute("INSERT INTO suppliers VALUES('sup:1','ACTIVE')")
    db.execute("INSERT INTO purchase_orders VALUES('po:1','sup:1','ORDERED',?,?)",(now.isoformat(),now.isoformat()))
    db.execute("INSERT INTO purchase_order_lines VALUES('line:1','po:1','drc:1',3,NULL)")
    db.execute("INSERT INTO stock_movements VALUES('m1','drc:1',-1,'SALE','APPLIED',?)",(now.isoformat(),))
    db.execute("INSERT INTO sale_events VALUES('drc:1','SALE',?,'1',NULL,NULL)",(now.isoformat(),)); db.commit()
    row=ReplenishmentEngine(path).snapshot("drc:1",at=now)
    assert row["observed_lead_days"] is None and row["estimated_stockout_date"] is None
    assert row["known_unit_cost"] is None and row["estimated_cost"] is None
    assert row["confidence"]=="PARTIAL" and row["incoming"]==3


def test_budget_and_evidence_do_not_invent_cash(tmp_path):
    path=tmp_path/"os.db"; db=database(path); db.commit(); engine=ReplenishmentEngine(path)
    assert engine.configure(purchasing_budget=None)["purchasing_budget"] is None
    evidence=engine.evidence()
    assert evidence["stock_value_coverage"] is None and evidence["suppliers_configured"]==0
