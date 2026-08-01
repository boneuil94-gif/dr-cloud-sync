import io,json
from decimal import Decimal
from urllib.error import HTTPError,URLError
import pytest

from dr_cloud_sync.sumup import SumUpProvider,SumUpError,SumUpTransactionLedger,PaymentSettlementLedger

class Response(io.BytesIO):
 def __enter__(self):return self
 def __exit__(self,*args):pass
class Secrets:
 def __init__(self,value="top-secret"):self.value=value
 def get(self,ref):return self.value

def provider(payloads,**kw):
 calls=[]
 def opener(request,timeout):
  calls.append(request)
  value=payloads.pop(0)
  if isinstance(value,Exception):raise value
  return Response(json.dumps(value).encode())
 return SumUpProvider("MERCHANT","sumup.production",Secrets(),opener=opener,sleep=lambda _:None,retries=kw.pop("retries",1),**kw),calls

def test_authentication_is_real_and_read_only():
 p,calls=provider([{"items":[]}]);assert p.health()["status"]=="CONNECTED"
 assert calls[0].method=="GET" and calls[0].full_url.endswith("/transactions/history?limit=1")
 assert calls[0].headers["Authorization"]=="Bearer top-secret"

def test_transactions_details_pagination_and_fields(tmp_path):
 history={"items":[{"id":"tx1","transaction_code":"CODE","amount":12,"currency":"EUR","timestamp":"2026-01-01T00:00:00+00:00"}],"next":"2026-01-02T00:00:00Z"}
 detail={"status":"SUCCESSFUL","fee_amount":"0.30","vat_amount":"2","tip_amount":"1","events":[{"type":"REFUND"}]}
 p,_=provider([history,detail,{"items":[] }]);ledger=SumUpTransactionLedger(tmp_path/"db.sqlite")
 result=ledger.sync(p);row=ledger.rows()[0]
 assert result["rows_imported"]==1 and row["fee"]=="0.30" and json.loads(row["events_json"])[0]["type"]=="REFUND"

def test_payout_deduction_link_and_idempotence(tmp_path):
 db=tmp_path/"db.sqlite";t=SumUpTransactionLedger(db);s=PaymentSettlementLedger(db)
 tx=type("P",(),{"rows":({"id":"tx1","transaction_code":"CODE","amount":"10","currency":"EUR","timestamp":"2026-01-01T00:00:00Z"},),"next_cursor":None})()
 assert t.import_page(tx)["rows_imported"]==1 and t.import_page(tx)["rows_imported"]==0
 payout={"id":"pay1","date":"2026-01-02","amount":"9.7","currency":"EUR","fee":".3","status":"PAID","deductions":[{"transaction_code":"CODE","type":"REFUND"}]}
 page=type("P",(),{"rows":(payout,),"next_cursor":None})();assert s.import_page(page)["rows_imported"]==1
 assert s.reconcile()==1 and s.import_page(page)["rows_imported"]==0

@pytest.mark.parametrize("code",[401,403,429,500])
def test_http_errors_are_sanitized(code):
 error=HTTPError("https://api.sumup.com",code,"bad",{},io.BytesIO(b'{"token":"top-secret"}'))
 p,_=provider([error]);
 with pytest.raises(SumUpError) as caught:p.health()
 assert "top-secret" not in str(caught.value) and caught.value.retryable==(code in (429,500))

def test_timeout_invalid_json_and_no_secret_leak():
 p,_=provider([URLError(TimeoutError())])
 with pytest.raises(SumUpError,match="Délai"):p.health()
 p,_=provider([b"bad"])
 p.opener=lambda *a,**k:Response(b"not-json")
 with pytest.raises(SumUpError,match="JSON"):p.health()

def test_sumup_schema_is_separate_from_sales_ledger(tmp_path):
 ledger=SumUpTransactionLedger(tmp_path/"db.sqlite")
 tables={r[0] for r in ledger.db.execute("select name from sqlite_master where type='table'")}
 assert "sumup_transactions" in tables and "sale_events" not in tables

def test_event_subledgers_partial_refund_chargeback_tip_tax_and_fee(tmp_path):
 ledger=SumUpTransactionLedger(tmp_path/"db.sqlite")
 row={"id":"tx","amount":"100","currency":"EUR","timestamp":"2026-01-01T00:00:00Z","tip_amount":"5","vat_amount":"16.67","fees":[{"id":"fee-1","type":"TRANSACTION","amount":"1.69","currency":"EUR"}],"events":[{"id":"refund-1","type":"REFUND","amount":"25","status":"SUCCESSFUL","timestamp":"2026-01-02T00:00:00Z"},{"id":"cb-1","type":"CHARGEBACK","amount":"10","reason":"FRAUD"},{"id":"rev-1","type":"REVERSAL","amount":"2"}]}
 ledger.import_page(type("P",(),{"rows":(row,),"next_cursor":None})())
 tx=ledger.rows()[0]
 assert (tx["tip_amount"],tx["vat_amount"],tx["refunded_amount"],tx["chargeback_amount"])==("5","16.67","25","10")
 assert ledger.db.execute("select is_partial from sumup_refunds").fetchone()[0]==1
 assert ledger.db.execute("select count(*) from sumup_fees").fetchone()[0]==1
 assert ledger.db.execute("select count(*) from sumup_transaction_events").fetchone()[0]==3

def test_merchant_and_readers_are_separate_and_sanitized(tmp_path):
 ledger=SumUpTransactionLedger(tmp_path/"db.sqlite")
 ledger.import_merchant({"merchant_profile":{"merchant_code":"MC","legal_name":"Dr Cloud","currency":"EUR","access_token":"must-not-persist"}})
 ledger.import_readers(type("P",(),{"rows":({"id":"R1","model":"SOLO","status":"ONLINE","software_version":"1.2"},),"next_cursor":None})())
 assert ledger.db.execute("select legal_name from sumup_merchants").fetchone()[0]=="Dr Cloud"
 assert "must-not-persist" not in ledger.db.execute("select raw_json from sumup_merchants").fetchone()[0]
 assert ledger.db.execute("select model from sumup_readers").fetchone()[0]=="SOLO"

def test_payout_dates_are_mandatory_and_more_than_100_is_paginated():
 first={"items":[{"id":f"p{i}","date":"2026-01-01"} for i in range(100)]}
 p,calls=provider([first,{"items":[]}],page_size=100)
 page=p.payouts(start_date="2026-01-01",end_date="2026-01-31")
 assert page.next_cursor and "start_date=2026-01-01" in calls[0].full_url and "end_date=2026-01-31" in calls[0].full_url
 p.payouts(page.next_cursor)
 assert "offset=100" in calls[1].full_url

def test_404_is_non_retryable_and_classified():
 p,_=provider([HTTPError("https://api.sumup.com",404,"missing",{},io.BytesIO())])
 with pytest.raises(SumUpError) as caught:p.health()
 assert not caught.value.retryable and caught.value.diagnostic["category"]=="NOT_FOUND"
