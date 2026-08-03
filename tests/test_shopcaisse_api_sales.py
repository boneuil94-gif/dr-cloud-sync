from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dr_cloud_sync.sales import SalesLedger
from dr_cloud_sync.sales_ingestion import SalesSyncService, ShopCaisseAPISalesProvider, canonicalize_payment_type


class Catalogue:
    def all(self):
        return [SimpleNamespace(drcloud_product_key="product:a", ean="123", reference="R1",
                                shopcaisse_item_id="item-1", combination_id=None)]


class API:
    def __init__(self):
        self.from_values=[]; self.fail=False
    def pull_stores(self): return [{"id":"store-1"}]
    def pull_store_sales(self,store_id,from_ms=None):
        self.from_values.append(from_ms)
        if self.fail: raise ConnectionError("network down")
        return [{"id":"ticket-1","timestamp":1785578400000,"status":"paid","type":"SALE",
                 "lines":[{"id":"line-1","item":{"id":"item-1","barcodes":["123"]},"quantity":1,
                           "unitPrice":12,"price":{"vatIncluded":12,"vatExcluded":10,"vatRate":.2}}],
                 "payments":[{"id":"payment-1","type":"CARD","amount":12,"name":"Carte"}]}]
    def pull_store_stocks(self,store_id,board_type):
        return [{"item":"item-1","stock":7,"reservedForCustomers":1,"reservedForSuppliers":2}]


def service(tmp_path):
    ledger=SalesLedger(tmp_path/"db",Catalogue()); api=API()
    provider=ShopCaisseAPISalesProvider(api,ledger.db)
    return SalesSyncService(ledger,{"SHOPCAISSE":provider}),api


def test_initial_incremental_payments_stock_and_duplicate_replay(tmp_path):
    sync,api=service(tmp_path)
    first=sync.sync("SHOPCAISSE"); second=sync.sync("SHOPCAISSE")
    assert first["sales"]==1 and first["imported"]==1 and first["payments"]==1
    assert second["duplicates"]==1 and second["payments"]==0
    assert api.from_values[0] is None and api.from_values[1]==1785578399999
    assert sync.db.execute("SELECT count(*) FROM sales").fetchone()[0]==1
    assert sync.db.execute("SELECT count(*) FROM sale_payments").fetchone()[0]==1
    assert tuple(sync.db.execute("SELECT stock,reserved_customers,reserved_suppliers FROM shopcaisse_stock_observations").fetchone())==("7","1","2")
    diagnostic=sync.diagnostics()[0]
    assert diagnostic["sales_count"]==1 and diagnostic["payments_count"]==1 and diagnostic["freshness"]=="FRESH"


def test_network_failure_preserves_cursor_then_resumes(tmp_path):
    sync,api=service(tmp_path); api.fail=True
    with pytest.raises(ConnectionError): sync.sync("SHOPCAISSE")
    state=sync.db.execute("SELECT status,cursor FROM sales_sync_states WHERE source='SHOPCAISSE'").fetchone()
    assert tuple(state)==("ERROR",None)
    api.fail=False
    assert sync.sync("SHOPCAISSE")["imported"]==1


@pytest.mark.parametrize(("raw","expected"),[("CARD","CARD"),(" Visa ","CARD"),("Mastercard","CARD"),("cb","CARD"),
    (" espèces ","CASH"),("avoir","STORE_CREDIT"),("VIREMENT","BANK_TRANSFER"),("carte cadeau","GIFT_CARD"),
    ("quelque chose","UNKNOWN"),(None,"UNKNOWN")])
def test_real_shopcaisse_payment_labels_are_explicitly_canonicalized(raw,expected):
    category,rule,version=canonicalize_payment_type(raw)
    assert category==expected and rule and version=="shopcaisse-payment-types-v1"


def test_payment_mapping_and_source_fields_are_persisted(tmp_path):
    sync,_=service(tmp_path); sync.sync("SHOPCAISSE")
    row=sync.db.execute("SELECT payment_type,canonical_payment_type,mapping_rule,mapping_version,currency,occurred_at,source,store_id,quality_status FROM sale_payments").fetchone()
    assert tuple(row)[0:2]==("CARD","CARD")
    assert row[2].startswith("exact-normalized") and row[3]=="shopcaisse-payment-types-v1"
    assert tuple(row)[4:]==("EUR","2026-08-01T10:00:00+00:00","SHOPCAISSE","store-1","VALID")
