"""Deterministic, evidence-only stock and replenishment projections."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS replenishment_settings(
 id INTEGER PRIMARY KEY CHECK(id=1), safety_stock_days REAL NOT NULL DEFAULT 7,
 target_coverage_days REAL NOT NULL DEFAULT 30, purchasing_budget TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_suggestions(
 suggestion_id TEXT PRIMARY KEY, product_key TEXT NOT NULL, supplier_id TEXT,
 status TEXT NOT NULL, suggested_quantity INTEGER NOT NULL, estimated_cost TEXT,
 confidence TEXT NOT NULL, reason TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_open_purchase_suggestion
 ON purchase_suggestions(product_key) WHERE status IN ('PROPOSED','REVIEWED','APPROVED');
"""


def _now(): return datetime.now(timezone.utc).isoformat()
def _number(value): return Decimal(str(value)) if value is not None else None


class ReplenishmentEngine:
    """Build auditable projections without filling unknown facts with estimates."""
    STATUSES={"PROPOSED","REVIEWED","APPROVED","REJECTED","ORDERED"}

    def __init__(self, path: Path, catalogue=None):
        self.db=sqlite3.connect(path,check_same_thread=False); self.db.row_factory=sqlite3.Row
        with self.db:
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO replenishment_settings(id,updated_at) VALUES(1,?)",(_now(),))
        self.catalogue=catalogue

    def configure(self, *, safety_stock_days=None, target_coverage_days=None, purchasing_budget="UNCHANGED"):
        current=dict(self.db.execute("SELECT * FROM replenishment_settings WHERE id=1").fetchone())
        safety=float(current["safety_stock_days"] if safety_stock_days is None else safety_stock_days)
        target=float(current["target_coverage_days"] if target_coverage_days is None else target_coverage_days)
        if safety < 0 or target <= 0: raise ValueError("coverage settings must be positive")
        budget=current["purchasing_budget"] if purchasing_budget=="UNCHANGED" else (None if purchasing_budget is None else str(_number(purchasing_budget)))
        if budget is not None and _number(budget)<0: raise ValueError("purchasing_budget must be positive")
        with self.db:self.db.execute("UPDATE replenishment_settings SET safety_stock_days=?,target_coverage_days=?,purchasing_budget=?,updated_at=? WHERE id=1",(safety,target,budget,_now()))
        return dict(self.db.execute("SELECT * FROM replenishment_settings WHERE id=1").fetchone())

    def observed_lead_days(self, supplier_id):
        row=self.db.execute("""SELECT AVG(julianday(g.received_at)-julianday(p.ordered_at)) value
          FROM purchase_orders p JOIN goods_receipts g ON g.purchase_order_id=p.purchase_order_id
          WHERE p.supplier_id=? AND p.ordered_at IS NOT NULL AND g.status='APPLIED'
          AND julianday(g.received_at)>=julianday(p.ordered_at)""",(supplier_id,)).fetchone()
        return float(row["value"]) if row and row["value"] is not None else None

    def _product_keys(self):
        if self.catalogue: return [p.drcloud_product_key for p in self.catalogue.all()]
        sql="""SELECT drcloud_product_key key FROM stock_movements UNION SELECT product_key FROM sale_events
               UNION SELECT product_key FROM purchase_order_lines"""
        try:return [r[0] for r in self.db.execute(sql) if r[0]]
        except sqlite3.OperationalError:return []

    def snapshot(self, product_key, *, at=None):
        at=at or datetime.now(timezone.utc); settings=dict(self.db.execute("SELECT * FROM replenishment_settings WHERE id=1").fetchone())
        stock=self.db.execute("SELECT SUM(quantity_delta) q,MAX(CASE WHEN movement_type='SUPPLIER_RECEIPT' THEN applied_at END) receipt FROM stock_movements WHERE drcloud_product_key=? AND status='APPLIED'",(product_key,)).fetchone()
        on_hand=int(stock["q"]) if stock and stock["q"] is not None else None
        sales=self.db.execute("""SELECT
          SUM(CASE WHEN sold_at>=? THEN CAST(quantity AS REAL) ELSE 0 END) sold7,
          SUM(CAST(quantity AS REAL)) sold30,MAX(sold_at) last_sale,
          SUM(CASE WHEN line_total_ht IS NOT NULL THEN CAST(line_total_ht AS REAL) END) revenue,
          SUM(CASE WHEN cost_basis IS NOT NULL THEN CAST(cost_basis AS REAL) END) cost
          FROM sale_events WHERE product_key=? AND event_kind='SALE' AND sold_at>=? AND sold_at<=?""",
          ((at-timedelta(days=7)).isoformat(),product_key,(at-timedelta(days=30)).isoformat(),at.isoformat())).fetchone()
        sold7=float(sales["sold7"] or 0); sold30=float(sales["sold30"] or 0); velocity7=sold7/7; velocity30=sold30/30
        incoming_row=self.db.execute("""SELECT SUM(l.ordered_quantity-COALESCE(r.received,0)) q FROM purchase_order_lines l
          JOIN purchase_orders p ON p.purchase_order_id=l.purchase_order_id LEFT JOIN
          (SELECT gl.purchase_order_line_id,SUM(gl.received_quantity) received FROM goods_receipt_lines gl JOIN goods_receipts g ON g.receipt_id=gl.receipt_id WHERE g.status='APPLIED' GROUP BY gl.purchase_order_line_id) r
          ON r.purchase_order_line_id=l.line_id WHERE l.product_key=? AND p.status IN ('ORDERED','PARTIALLY_RECEIVED')""",(product_key,)).fetchone()
        incoming=int(incoming_row["q"] or 0); available=on_hand  # no reservation ledger: never fabricate reservations
        supplier_rows=self.db.execute("""SELECT p.supplier_id,l.unit_cost FROM purchase_order_lines l JOIN purchase_orders p ON p.purchase_order_id=l.purchase_order_id
          WHERE l.product_key=? AND p.status!='CANCELLED' ORDER BY COALESCE(p.ordered_at,p.created_at) DESC""",(product_key,)).fetchall()
        supplier_id=supplier_rows[0]["supplier_id"] if supplier_rows else None
        unit_cost=next((_number(r["unit_cost"]) for r in supplier_rows if r["unit_cost"] is not None),None)
        lead=self.observed_lead_days(supplier_id) if supplier_id else None
        velocity=max(velocity7,velocity30); cover=(available/velocity if available is not None and velocity>0 else None)
        # A stockout date is deliberately withheld when supplier lead-time is
        # unknown: presenting it beside a reorder recommendation would imply a
        # complete planning horizon which the evidence does not support.
        stockout=(at+timedelta(days=cover)).date().isoformat() if cover is not None and lead is not None else None
        needed=None
        if available is not None:
            horizon=float(settings['safety_stock_days'])+(lead or 0)
            needed=max(0,int(Decimal(str(velocity*horizon-available-incoming)).to_integral_value(rounding=ROUND_CEILING)))
        confidence="COMPLETE" if lead is not None and available is not None and supplier_id else "PARTIAL"
        trend="RISING" if velocity7>velocity30*1.15 else "FALLING" if velocity7<velocity30*.85 else "STABLE"
        return {"product_key":product_key,"on_hand":on_hand,"reserved":None,"available":available,"incoming":incoming,
          "sold_7d":sold7,"sold_30d":sold30,"sales_velocity_7d":velocity7,"sales_velocity_30d":velocity30,
          "trend":trend,"last_sale_at":sales["last_sale"],"last_receipt_at":stock["receipt"] if stock else None,
          "coverage_days":cover,"days_of_cover":cover,"estimated_stockout_date":stockout,"supplier_id":supplier_id,
          "observed_lead_days":lead,"reorder_needed":needed is not None and needed>0,"suggested_quantity":needed,
          "known_unit_cost":str(unit_cost) if unit_cost is not None else None,"estimated_cost":str(unit_cost*needed) if unit_cost is not None and needed is not None else None,
          "revenue_30d":sales["revenue"],"known_cost_30d":sales["cost"],"confidence":confidence}

    def refresh(self):
        snapshots=[self.snapshot(k) for k in self._product_keys()]; created=[]
        for item in snapshots:
            if not item["reorder_needed"]: continue
            reason=f"available={item['available']}; incoming={item['incoming']}; velocity={max(item['sales_velocity_7d'],item['sales_velocity_30d']):.4f}; lead={item['observed_lead_days'] if item['observed_lead_days'] is not None else 'UNKNOWN'}"
            sid="reorder:"+str(uuid.uuid4()); stamp=_now()
            try:
                with self.db:self.db.execute("INSERT INTO purchase_suggestions VALUES(?,?,?,?,?,?,?,?,?,?,?)",(sid,item["product_key"],item["supplier_id"],"PROPOSED",item["suggested_quantity"],item["estimated_cost"],item["confidence"],reason,json.dumps(item,sort_keys=True),stamp,stamp))
                created.append(sid)
            except sqlite3.IntegrityError: pass
        return {"products":snapshots,"suggestions_generated":len(created),"suggestion_ids":created}

    def transition(self, suggestion_id, status):
        if status not in self.STATUSES: raise ValueError("invalid suggestion status")
        row=self.db.execute("SELECT status FROM purchase_suggestions WHERE suggestion_id=?",(suggestion_id,)).fetchone()
        if not row: raise KeyError("suggestion not found")
        allowed={"PROPOSED":{"REVIEWED","REJECTED"},"REVIEWED":{"APPROVED","REJECTED"},"APPROVED":{"ORDERED","REJECTED"}}
        if status!=row[0] and status not in allowed.get(row[0],set()): raise ValueError("invalid suggestion transition")
        with self.db:self.db.execute("UPDATE purchase_suggestions SET status=?,updated_at=? WHERE suggestion_id=?",(status,_now(),suggestion_id))
        return dict(self.db.execute("SELECT * FROM purchase_suggestions WHERE suggestion_id=?",(suggestion_id,)).fetchone())

    def evidence(self):
        products=[self.snapshot(k) for k in self._product_keys()]
        anomalies=[]
        for p in products:
            if p["on_hand"] is not None and p["on_hand"]<0: anomalies.append({"kind":"NEGATIVE_STOCK","product_key":p["product_key"]})
            if p["sold_30d"] and p["on_hand"] is None: anomalies.append({"kind":"SOLD_WITHOUT_KNOWN_STOCK","product_key":p["product_key"]})
        valued=[p for p in products if p["known_unit_cost"] is not None]
        return {"products_with_stock":sum(p["on_hand"] is not None for p in products),"products_without_stock":sum(p["on_hand"] is None for p in products),
          "products_with_cost":len(valued),"products_without_cost":len(products)-len(valued),"stock_value_coverage":(len(valued)/len(products)*100 if products else None),
          "reorder_suggestions_count":self.db.execute("SELECT count(*) FROM purchase_suggestions WHERE status IN ('PROPOSED','REVIEWED','APPROVED')").fetchone()[0],
          "suppliers_configured":self.db.execute("SELECT count(*) FROM suppliers WHERE status='ACTIVE'").fetchone()[0],
          "purchase_orders_open":self.db.execute("SELECT count(*) FROM purchase_orders WHERE status IN ('ORDERED','PARTIALLY_RECEIVED')").fetchone()[0],
          "last_stock_refresh_at":_now(),"stock_anomalies":anomalies}
