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

def test_ambiguous_and_unmatched_are_preserved(tmp_path):
    catalogue=Catalogue();catalogue.items.append(SimpleNamespace(drcloud_product_key="product:b",name="B",ean="123",reference="OTHER",shopcaisse_item_id="10",combination_id="23"))
    ledger=SalesLedger(tmp_path/"db",catalogue)
    sale=CanonicalSale("SHOPCAISSE","x","2026-07-30T10:00:00+00:00","UTC","STORE","EUR","COMPLETED",(CanonicalSaleLine("1",Decimal(1),source_ean="123"),CanonicalSaleLine("2",Decimal(1),source_ean="none")))
    class Fake:
        source="SHOPCAISSE"; configured=True
        def fetch(self,**kwargs): return ProviderBatch((sale,),"c1")
    service=SalesSyncService(ledger,{"SHOPCAISSE":Fake()});report=service.sync("SHOPCAISSE")
    assert report["ambiguous"]==1 and report["unmatched"]==1 and len(service.unmatched())==2
