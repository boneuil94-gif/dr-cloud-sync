from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dr_cloud_sync.sales import SalesLedger, SocialAnalyticsService


class Catalogue:
    def __init__(self):
        self.product=SimpleNamespace(drcloud_product_key="product:a",name="A",ean="123",reference="REF-A",shopcaisse_item_id="9")
    def all(self): return [self.product]


HEADER="source,external_sale_id,external_line_id,sold_at,timezone,event_kind,product_key,quantity,line_total_ttc,currency\n"


def test_preview_apply_idempotence_refund_unmatched_and_metrics(tmp_path):
    ledger=SalesLedger(tmp_path/"db.sqlite",Catalogue())
    content=HEADER+(
        "IMPORT,o1,1,2026-07-29T12:00:00+00:00,UTC,SALE,product:a,10,100,EUR\n"
        "IMPORT,o2,1,2026-07-30T12:00:00+00:00,UTC,REFUND,product:a,2,20,EUR\n"
        "IMPORT,o3,1,2026-07-30T13:00:00+00:00,UTC,SALE,missing,1,10,EUR\n")
    preview=ledger.preview_csv(content)
    assert (preview["valid"],preview["matched"],preview["unmatched"],preview["invalid"])==(3,2,1,0)
    applied=ledger.apply_csv(preview["batch_id"],content)
    assert applied=={"batch_id":preview["batch_id"],"inserted":3,"duplicates":0,"unmatched":1}
    again=ledger.preview_csv(content)
    assert again["duplicates"]==3 and not again["can_apply"]
    metrics=ledger.metrics("product:a",7,as_of=datetime(2026,7,31,tzinfo=timezone.utc))
    assert metrics["units_sold"]==8
    assert metrics["revenue_ttc"]==80
    assert metrics["gross_margin"] is None and not metrics["gross_margin_available"]


def test_invalid_data_and_changed_preview_are_rejected(tmp_path):
    ledger=SalesLedger(tmp_path/"db.sqlite",Catalogue())
    bad=HEADER+"IMPORT,o1,1,2026-07-29,UTC,SALE,product:a,-1,,\n"
    report=ledger.preview_csv(bad)
    assert report["invalid"]==1 and not report["can_apply"]
    good=HEADER+"IMPORT,o1,1,2026-07-29T00:00:00Z,UTC,SALE,product:a,1,,\n"
    report=ledger.preview_csv(good)
    with pytest.raises(ValueError,match="differs"):
        ledger.apply_csv(report["batch_id"],good+"\n")


def test_missing_revenue_is_not_zero(tmp_path):
    ledger=SalesLedger(tmp_path/"db.sqlite",Catalogue())
    content=HEADER+"IMPORT,o1,1,2026-07-29T00:00:00Z,UTC,SALE,product:a,2,,\n"
    preview=ledger.preview_csv(content);ledger.apply_csv(preview["batch_id"],content)
    value=ledger.metrics("product:a",7,as_of=datetime(2026,7,31,tzinfo=timezone.utc))
    assert value["units_sold"]==2 and value["revenue_ttc"] is None


class Provider:
    configured=True
    def fetch(self,post_id): return {"reach":100,"views":None,"clicks":4}


def test_social_snapshot_update_preserves_nullable_metrics(tmp_path):
    ledger=SalesLedger(tmp_path/"db.sqlite",Catalogue())
    analytics=SocialAnalyticsService(ledger.db,Provider())
    analytics.refresh("post:1","FAKE"); analytics.refresh("post:1","FAKE")
    summary=analytics.summary()
    assert len(summary["posts"])==1
    assert summary["averages"]["reach"]==100 and summary["averages"]["views"] is None
    assert summary["averages"]["conversions"] is None


def test_disabled_social_provider(tmp_path):
    ledger=SalesLedger(tmp_path/"db.sqlite",Catalogue())
    with pytest.raises(RuntimeError,match="disabled"):
        SocialAnalyticsService(ledger.db).refresh("post:1","NONE")
