from pathlib import Path
from dr_cloud_sync.sales_ingestion import ShopCaisseSalesProvider

def test_real_export_inbox_combines_files_and_refunds(tmp_path:Path):
    header="sale_id,line_id,sold_at,quantity,event_kind,item_id,ean,reference,line_total_ttc"
    (tmp_path/'1.csv').write_text(header+"\nT1,L1,2026-07-30T10:00:00Z,1,SALE,I1,123,R1,12\n")
    (tmp_path/'2.csv').write_text(header+"\nT2,L2,2026-07-30T11:00:00Z,1,REFUND,I1,123,R1,-12\n")
    batch=ShopCaisseSalesProvider(tmp_path).fetch()
    assert len(batch.sales)==2 and batch.sales[1].lines[0].kind=="REFUND"

def test_inbox_is_not_configured_until_directory_exists(tmp_path):
    assert ShopCaisseSalesProvider(tmp_path/'missing').configured is False
