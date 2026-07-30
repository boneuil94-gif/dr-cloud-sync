from datetime import datetime, timezone
from decimal import Decimal

from dr_cloud_sync.sales import SQLiteSalesRepository, SalesService


def payload(identity="42", *, status="COMPLETED", channel="ONLINE", day="2026-07-20", product="drc:1", currency="EUR", refund="0"):
    return {"source_sale_id":identity,"occurred_at":day+"T10:00:00+00:00","status":status,"channel":channel,"currency":currency,"gross_total":"24.00","discount_total":"4.00","net_total":"20.00","refund_total":refund,"lines":[{"source_line_id":"1","product_key":product,"source_product_reference":"PS-1","quantity":2,"unit_price":"12.00","discount_amount":"4.00","line_total":"20.00"}]}


def test_import_is_idempotent_and_preserves_historical_money(tmp_path):
    service=SalesService(SQLiteSalesRepository(tmp_path/"sales.db"))
    assert service.import_batch("PRESTASHOP",[payload()]) == {"created":1,"unchanged":0}
    assert service.import_batch("PRESTASHOP",[payload()]) == {"created":0,"unchanged":1}
    rows,total=service.repository.list()
    assert total==1 and rows[0].net_total==Decimal("20.00")
    assert service.repository.lines_for_sale(rows[0].sale_id)[0].unit_price==Decimal("12.00")


def test_sources_never_deduplicate_without_shared_external_identity(tmp_path):
    service=SalesService(SQLiteSalesRepository(tmp_path/"sales.db"))
    service.import_batch("PRESTASHOP",[payload()]); service.import_batch("SHOPCAISSE",[payload(channel="STORE")])
    assert service.repository.list()[1] == 2


def test_analytics_status_refund_channel_top_and_series(tmp_path):
    service=SalesService(SQLiteSalesRepository(tmp_path/"sales.db"))
    service.import_batch("PRESTASHOP",[payload("ok"),payload("cancel",status="CANCELLED"),payload("partial",status="PARTIALLY_REFUNDED",refund="5"),payload("store",channel="STORE",product=None)])
    stats=service.statistics("2026-07-01","2026-08-01","ONLINE")
    assert stats == {"available":True,"currency":"EUR","revenue":"35.00","sale_count":2,"units":4,"average_basket":"17.50","period":{"start":"2026-07-01","end":"2026-08-01"},"channel":"ONLINE"}
    assert service.top_products("2026-07-01","2026-08-01",sort="units")[0]["units"]==4
    assert service.top_products("2026-07-01","2026-08-01",sort="revenue")[0]["revenue"]=="40.00"
    assert service.daily_series("2026-07-01","2026-08-01")==[{"date":"2026-07-20","revenue":"55.00","sales":3,"units":6}]


def test_absent_and_multi_currency_data_are_not_misrepresented(tmp_path):
    service=SalesService(SQLiteSalesRepository(tmp_path/"sales.db"))
    assert service.statistics("2026-01-01","2026-02-01")["available"] is False
    service.import_batch("PRESTASHOP",[payload("eur")]); service.import_batch("SHOPCAISSE",[payload("usd",currency="USD")])
    assert service.statistics("2026-07-01","2026-08-01")["reason"] == "MULTI_CURRENCY"


def test_unmapped_lines_are_kept_but_excluded_from_product_ranking(tmp_path):
    service=SalesService(SQLiteSalesRepository(tmp_path/"sales.db"))
    sale,lines=service.normalize("SHOPCAISSE",payload(product=None))
    assert lines[0].mapping_status=="UNMAPPED"
    service.repository.import_batch([(sale,lines)])
    assert service.top_products("2026-07-01","2026-08-01")==[]


def test_batch_validation_is_atomic_and_quantity_must_be_positive(tmp_path):
    service=SalesService(SQLiteSalesRepository(tmp_path/"sales.db")); invalid=payload("bad"); invalid["lines"][0]["quantity"]=0
    try: service.import_batch("PRESTASHOP",[payload("valid"),invalid])
    except ValueError: pass
    assert service.repository.list()[1]==0


def test_source_quality_cursor_is_persistent(tmp_path):
    repo=SQLiteSalesRepository(tmp_path/"sales.db"); stamp=datetime.now(timezone.utc).isoformat()
    repo.set_source_state("PRESTASHOP","OK",stamp,"cursor-42")
    repo.set_source_state("SHOPCAISSE","PARTIAL",message="Sales endpoint not established")
    assert [x["quality"] for x in repo.source_states()]==["OK","PARTIAL"]
