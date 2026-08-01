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
