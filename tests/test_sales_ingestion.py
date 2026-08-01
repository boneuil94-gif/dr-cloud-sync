from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from dr_cloud_sync.sales import SalesLedger
from dr_cloud_sync.sales_ingestion import (CanonicalSale, CanonicalSaleLine, PrestaShopSalesProvider,
    ProviderBatch, SalesSyncService, ShopCaisseCSVProvider)

class Catalogue:
    def __init__(self): self.items=[SimpleNamespace(drcloud_product_key="product:a",name="A",ean="123",reference="REF",shopcaisse_item_id="9",combination_id="22")]
    def all(self): return self.items

def test_shopcaisse_preview_sync_idempotence_refund_and_no_stock(tmp_path):
    ledger=SalesLedger(tmp_path/"db",Catalogue()); content="sale_id,line_id,sold_at,quantity,event_kind,item_id,line_total_ttc,currency\n1,1,2026-07-30T10:00:00+02:00,2,SALE,9,20,EUR\n2,1,2026-07-30T11:00:00+02:00,1,REFUND,9,10,EUR\n"
    provider=ShopCaisseCSVProvider(content); service=SalesSyncService(ledger,{"SHOPCAISSE":provider})
    assert service.preview(provider)["matched"]==2
    first=service.sync("SHOPCAISSE");second=service.sync("SHOPCAISSE",force=True)
    assert first["imported"]==2 and first["refunds"]==1 and second["duplicates"]==2
    assert ledger.metrics("product:a",7,as_of=datetime(2026,7,31,tzinfo=timezone.utc))["units_sold"]==1
    tables={r[0] for r in ledger.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "stock_movements" not in tables

class PS:
    def iter_resource(self,name):
        if name=="order_details": return iter([{"id":"d1","id_order":"7","product_id":"1","product_attribute_id":"22","product_quantity":"2","total_price_tax_incl":"24","total_price_tax_excl":"20"}])
        return iter([{"id":"7","current_state":"2","date_add":"2026-07-30T10:00:00+00:00","id_currency":"EUR"}])

def test_prestashop_only_configured_paid_states_and_combination_mapping(tmp_path):
    provider=PrestaShopSalesProvider(PS(),[2]);batch=provider.fetch();line=batch.sales[0].lines[0]
    assert line.source_variant_id=="22" and line.quantity==Decimal("2")
    ledger=SalesLedger(tmp_path/"db",Catalogue());result=SalesSyncService(ledger,{"PRESTASHOP":provider}).sync("PRESTASHOP")
    assert result["imported"]==1 and ledger.list_events()[0]["product_key"]=="product:a"

def test_prestashop_date_without_timezone_is_assumed_utc():
    assert PrestaShopSalesProvider._datetime("2026-07-30 10:00:00")=="2026-07-30T10:00:00+00:00"

def test_prestashop_iso_date_is_converted_to_utc():
    assert PrestaShopSalesProvider._datetime("2026-07-30T12:00:00+02:00")=="2026-07-30T10:00:00+00:00"
    assert PrestaShopSalesProvider._datetime("2026-07-30T10:00:00Z")=="2026-07-30T10:00:00+00:00"

def test_prestashop_invalid_date_is_rejected():
    try:
        PrestaShopSalesProvider._datetime("not-a-date")
    except ValueError as exc:
        assert str(exc)=="invalid PrestaShop date: not-a-date"
    else:
        raise AssertionError("invalid PrestaShop date was accepted")


def test_failed_sale_identity_and_exact_cause_are_exposed(tmp_path):
    class InvalidPS(PS):
        def iter_resource(self,name):
            if name=="order_details": return iter([])
            return iter([{"id":"bad-7","current_state":"2","date_add":"not-a-date"}])
    service=SalesSyncService(SalesLedger(tmp_path/"db",Catalogue()),{"PRESTASHOP":PrestaShopSalesProvider(InvalidPS(),[2])})
    try:
        service.sync("PRESTASHOP")
    except ValueError:
        pass
    diagnostic=next(item for item in service.diagnostics() if item["source"]=="PRESTASHOP")
    assert diagnostic["failed_count"]==1
    assert diagnostic["failed_sales"]==[{"sale":"bad-7","line":None,"error":"PrestaShop sale bad-7: invalid PrestaShop date: not-a-date"}]

def test_prestashop_utc_dates_are_persisted_in_sqlite(tmp_path):
    class NaivePS(PS):
        def iter_resource(self,name):
            if name=="order_details": return super().iter_resource(name)
            return iter([{"id":"7","current_state":"2","date_add":"2026-07-30 09:00:00",
                          "date_upd":"2026-07-30 10:00:00","id_currency":"EUR"}])

    ledger=SalesLedger(tmp_path/"db",Catalogue())
    service=SalesSyncService(ledger,{"PRESTASHOP":PrestaShopSalesProvider(NaivePS(),[2])})
    service.sync("PRESTASHOP")

    sale=dict(ledger.db.execute("SELECT * FROM sales WHERE source='PRESTASHOP'").fetchone())
    event=dict(ledger.db.execute("SELECT * FROM sale_events WHERE source='PRESTASHOP'").fetchone())
    state=dict(ledger.db.execute("SELECT * FROM sales_sync_states WHERE source='PRESTASHOP'").fetchone())
    assert sale["sold_at"]==event["sold_at"]=="2026-07-30T10:00:00+00:00"
    assert sale["source_updated_at"]==event["source_updated_at"]=="2026-07-30T10:00:00+00:00"
    for value in (sale["imported_at"],event["imported_at"],state["last_attempt_at"],state["last_success_at"]):
        parsed=datetime.fromisoformat(value)
        assert parsed.tzinfo is not None and parsed.utcoffset()==timezone.utc.utcoffset(parsed)

def test_ambiguous_and_unmatched_are_preserved(tmp_path):
    catalogue=Catalogue();catalogue.items.append(SimpleNamespace(drcloud_product_key="product:b",name="B",ean="123",reference="OTHER",shopcaisse_item_id="10",combination_id="23"))
    ledger=SalesLedger(tmp_path/"db",catalogue)
    sale=CanonicalSale("SHOPCAISSE","x","2026-07-30T10:00:00+00:00","UTC","STORE","EUR","COMPLETED",(CanonicalSaleLine("1",Decimal(1),source_ean="123"),CanonicalSaleLine("2",Decimal(1),source_ean="none")))
    class Fake:
        source="SHOPCAISSE"; configured=True
        def fetch(self,**kwargs): return ProviderBatch((sale,),"c1")
    service=SalesSyncService(ledger,{"SHOPCAISSE":Fake()});report=service.sync("SHOPCAISSE")
    assert report["ambiguous"]==1 and report["unmatched"]==1 and len(service.unmatched())==2

def test_shopcaisse_failure_diagnostic_contains_operator_fields(tmp_path):
    ledger=SalesLedger(tmp_path/"db",Catalogue())
    invalid=CanonicalSale("SHOPCAISSE","ticket-3","2026-08-01T09:30:00+00:00","UTC","STORE","EUR","COMPLETED",
        (CanonicalSaleLine("line-1",Decimal(0),line_total_ttc=Decimal("42.50")),),location="Paris")
    class Fake:
        source="SHOPCAISSE"; configured=True
        def fetch(self,**kwargs): return ProviderBatch((invalid,),"cursor")
    service=SalesSyncService(ledger,{"SHOPCAISSE":Fake()})
    assert service.sync("SHOPCAISSE")["invalid"]==1
    failure=service.failed_sales()["failures"][0]
    assert failure=={"shopcaisse_id":"ticket-3","date":"2026-08-01T09:30:00+00:00",
        "amount":"42.50","currency":"EUR","store":"Paris","stage":"INGESTION_LINE",
        "category":"VALIDATION","message":"invalid line identity or quantity",
        "retryable":False,"permanent":True}
