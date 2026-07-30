"""Read-only comparison of the local ledger with validated external snapshots."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from .domain import (ComparisonStatus, ExternalStockObservation,
                     ObservationFreshness, Product)
from .jobs import JobStatus, SqliteJobRepository

DEFAULT_STALE_AFTER = timedelta(hours=24)


class ExternalStockQueryService:
    """Queries persisted observations only; it has no external or ledger write port."""
    def __init__(self, path: Path, products: list[Product], stock_repository,
                 stale_after: timedelta = DEFAULT_STALE_AFTER):
        self.path, self.products, self.stock_repository = path, products, stock_repository
        self.stale_after = stale_after

    def _prestashop(self, now: datetime) -> tuple[dict[str, ExternalStockObservation], set[str]]:
        if not self.path.exists():
            return {}, set()
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row
        try:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"external_stock_observations","sync_runs"} <= tables:
                return {}, set()
            rows=db.execute("""SELECT o.* FROM external_stock_observations o JOIN sync_runs j ON j.job_id=o.job_id
              WHERE j.status='SUCCEEDED' AND o.source='PRESTASHOP' AND o.job_id=(
                SELECT o2.job_id FROM external_stock_observations o2 JOIN sync_runs j2 ON j2.job_id=o2.job_id
                WHERE o2.source='PRESTASHOP' AND j2.status='SUCCEEDED' ORDER BY o2.observed_at DESC LIMIT 1)""").fetchall()
        finally: db.close()
        by_identity: dict[tuple[str,str], list[Product]]={}
        for product in self.products:
            identity=(str(product.product_id),str(product.combination_id or 0))
            by_identity.setdefault(identity,[]).append(product)
        result, ambiguous={}, set()
        for row in rows:
            matches=by_identity.get((row["source_product_id"],row["source_combination_id"]),[])
            if len(matches) != 1:
                ambiguous.update(p.drcloud_product_key for p in matches); continue
            observed=datetime.fromisoformat(row["observed_at"])
            freshness=ObservationFreshness.FRESH if now-observed <= self.stale_after else ObservationFreshness.STALE
            product=matches[0]
            result[product.drcloud_product_key]=ExternalStockObservation(product.drcloud_product_key,"PRESTASHOP",row["quantity"],row["observed_at"],row["job_id"],freshness)
        return result, ambiguous

    def comparisons(self, now: datetime | None = None) -> list[dict]:
        now=now or datetime.now(timezone.utc); observations, ambiguous=self._prestashop(now)
        local={r["drcloud_product_key"]:int(r["quantity"]) for r in self.stock_repository.current_positions()}
        rows=[]
        for product in self.products:
            observation=observations.get(product.drcloud_product_key); quantity=local.get(product.drcloud_product_key,0)
            if product.drcloud_product_key in ambiguous: status=ComparisonStatus.INCONSISTENT
            elif observation is None: status=ComparisonStatus.UNKNOWN
            elif observation.freshness is ObservationFreshness.STALE: status=ComparisonStatus.STALE
            elif quantity == observation.quantity: status=ComparisonStatus.MATCH
            else: status=ComparisonStatus.DIFFERENCE
            rows.append({"drcloud_product_key":product.drcloud_product_key,"name":product.display_name,"reference":product.reference,
              "local_quantity":quantity,"prestashop":asdict(observation) if observation else None,
              "shopcaisse":None,"difference_prestashop":quantity-observation.quantity if observation else None,
              "difference_shopcaisse":None,"status":status.value})
        return rows

    def statistics(self) -> dict[str,int]:
        rows=self.comparisons()
        return {"matching":sum(r["status"]=="MATCH" for r in rows),"differences":sum(r["status"]=="DIFFERENCE" for r in rows),
          "stale":sum(r["status"]=="STALE" for r in rows),"unknown":sum(r["status"] in {"UNKNOWN","INCONSISTENT"} for r in rows)}

    def sync_status(self) -> dict:
        jobs=SqliteJobRepository(self.path).list_recent(100)
        jobs=[j for j in jobs if j.connector=="PRESTASHOP" and j.operation=="SNAPSHOT_PULL"]
        successful=next((j for j in jobs if j.status is JobStatus.SUCCEEDED),None)
        failed=next((j for j in jobs if j.status in {JobStatus.FAILED,JobStatus.RETRYABLE}),None)
        return {"last_successful_job":asdict(successful) if successful else None,
                "last_failed_job":asdict(failed) if failed else None,"shopcaisse":"UNAVAILABLE"}
