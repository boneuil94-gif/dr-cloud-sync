import io,json,re
from pathlib import Path
from decimal import Decimal
from urllib.error import HTTPError,URLError
import pytest

from dr_cloud_sync.sumup import (PAYOUT_COLUMNS, PAYOUT_MAX_LIMIT, PaymentSettlementLedger,
                                 SumUpError, SumUpProvider, SumUpTransactionLedger,
                                 _payout_values)

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
 assert s.cockpit()["revenue_included"] is False

def test_payout_insert_values_match_sqlite_schema(tmp_path):
 ledger=PaymentSettlementLedger(tmp_path/"db.sqlite")
 sqlite_columns=tuple(row[1] for row in ledger.db.execute("PRAGMA table_info(sumup_payouts)"))
 values=_payout_values({"id":"pay1","date":"2026-01-02","amount":"9.7"},"pay1","now")
 assert sqlite_columns==PAYOUT_COLUMNS
 assert len(sqlite_columns)==len(values)
 ledger.db.close()

def test_legacy_payout_schema_is_migrated_idempotently_without_data_loss(tmp_path):
 path=tmp_path/"legacy.sqlite"
 db=__import__("sqlite3").connect(path)
 db.execute("""CREATE TABLE sumup_payouts(
  payout_id TEXT PRIMARY KEY,type TEXT,payout_date TEXT NOT NULL,amount TEXT NOT NULL,
  currency TEXT NOT NULL,fee TEXT NOT NULL,status TEXT,reference TEXT,transaction_code TEXT,
  deductions_json TEXT NOT NULL,raw_json TEXT NOT NULL,imported_at TEXT NOT NULL)""")
 db.execute("INSERT INTO sumup_payouts (payout_id,payout_date,amount,currency,fee,deductions_json,raw_json,imported_at) VALUES (?,?,?,?,?,?,?,?)",
            ("legacy","2026-01-01","10","EUR","0","[]","{}","then"))
 db.commit();db.close()
 first=PaymentSettlementLedger(path);first.db.close()
 second=PaymentSettlementLedger(path)
 columns={row[1] for row in second.db.execute("PRAGMA table_info(sumup_payouts)")}
 assert {"start_date","end_date","paid_date"} <= columns
 assert second.db.execute("SELECT amount FROM sumup_payouts WHERE payout_id='legacy'").fetchone()[0]=="10"
 second.db.close()

def test_connector_inserts_always_name_their_columns():
 root=Path(__file__).parents[1]/"src"/"dr_cloud_sync"
 connector_files=("sumup.py","sales_ingestion.py","store.py","prestashop.py","shopcaisse.py","qonto.py","bank.py")
 bare_insert=re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s+VALUES\s*\(",re.IGNORECASE)
 violations=[name for name in connector_files if bare_insert.search((root/name).read_text())]
 assert violations==[]

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
 assert ledger.db.execute("select count(*) from sumup_reversals").fetchone()[0]==1

def test_merchant_and_readers_are_separate_and_sanitized(tmp_path):
 ledger=SumUpTransactionLedger(tmp_path/"db.sqlite")
 ledger.import_merchant({"merchant_profile":{"merchant_code":"MC","legal_name":"Dr Cloud","currency":"EUR","created_at":"2026-08-01T09:00:00Z","updated_at":"2026-08-27T09:00:00Z","access_token":"must-not-persist"}})
 ledger.import_readers(type("P",(),{"rows":({"id":"R1","model":"SOLO","status":"ONLINE","software_version":"1.2"},),"next_cursor":None})())
 assert ledger.db.execute("select legal_name from sumup_merchants").fetchone()[0]=="Dr Cloud"
 assert "must-not-persist" not in ledger.db.execute("select raw_json from sumup_merchants").fetchone()[0]
 assert ledger.db.execute("select model from sumup_readers").fetchone()[0]=="SOLO"

def test_sensitive_card_material_is_never_persisted(tmp_path):
 ledger=SumUpTransactionLedger(tmp_path/"db.sqlite")
 row={"id":"tx","amount":"1","currency":"EUR","timestamp":"2026-01-01T00:00:00Z",
      "card":{"scheme":"VISA","pan":"4111111111111111","cvv":"123","payment_token":"token"}}
 ledger.import_page(type("P",(),{"rows":(row,),"next_cursor":None})())
 raw=ledger.db.execute("select raw_json from sumup_transactions").fetchone()[0]
 assert "4111111111111111" not in raw and '123' not in raw and 'token' not in raw

def test_payout_contract_uses_documented_limit_order_and_no_offset():
 p,calls=provider([[{"id":"p1","date":"2026-01-01","amount":1}]])
 page=p.payouts(start_date="2026-01-01",end_date="2026-01-01")
 assert len(page.rows)==1 and page.next_cursor is None
 url=calls[0].full_url
 assert "start_date=2026-01-01" in url and "end_date=2026-01-01" in url
 assert f"limit={PAYOUT_MAX_LIMIT}" in url and "order=asc" in url and "format=json" in url
 assert "offset=" not in url

def test_payout_windows_advance_without_inclusive_boundary_overlap():
 p,calls=provider([[{"id":"p1","date":"2026-01-02"}],[{"id":"p2","date":"2026-01-04"}]],window_days=2)
 first=p.payouts(start_date="2026-01-01",end_date="2026-01-04")
 assert "start_date=2026-01-01" in calls[0].full_url and "end_date=2026-01-02" in calls[0].full_url
 second=p.payouts(first.next_cursor)
 assert "start_date=2026-01-03" in calls[1].full_url and "end_date=2026-01-04" in calls[1].full_url
 assert second.next_cursor is None and all("offset=" not in call.full_url for call in calls)

def test_saturated_multi_day_payout_window_is_split_before_data_is_returned():
 saturated=[{"id":f"p{i}","date":"2026-01-01"} for i in range(PAYOUT_MAX_LIMIT)]
 p,calls=provider([saturated,[{"id":"safe","date":"2026-01-01"}]],window_days=4)
 page=p.payouts(start_date="2026-01-01",end_date="2026-01-04")
 assert [row["id"] for row in page.rows]==["safe"]
 assert "start_date=2026-01-01" in calls[0].full_url and "end_date=2026-01-04" in calls[0].full_url
 assert "start_date=2026-01-01" in calls[1].full_url and "end_date=2026-01-02" in calls[1].full_url
 assert all("offset=" not in call.full_url for call in calls)
 state=json.loads(page.next_cursor)
 assert state["start"]=="2026-01-03" and state["final"]=="2026-01-04"

def test_saturated_single_day_payout_window_fails_closed():
 saturated=[{"id":f"p{i}","date":"2026-01-01"} for i in range(PAYOUT_MAX_LIMIT)]
 p,calls=provider([saturated])
 with pytest.raises(SumUpError) as caught:
  p.payouts(start_date="2026-01-01",end_date="2026-01-01")
 assert caught.value.diagnostic["stage"]=="pagination"
 assert caught.value.diagnostic["category"]=="VALIDATION"
 assert len(calls)==1 and "offset=" not in calls[0].full_url

def test_payout_sync_reports_durable_records_available(tmp_path):
 p,_=provider([[{"id":"p1","date":"2026-01-01","amount":"9.7","currency":"EUR","fee":".3","status":"SUCCESSFUL"}]])
 ledger=PaymentSettlementLedger(tmp_path/"db.sqlite")
 result=ledger.sync(p,start_date="2026-01-01",end_date="2026-01-01")
 assert result["rows_imported"]==1
 assert result["records_available"]==1
 assert result["cursor"] is None

def test_404_is_non_retryable_and_classified():
 p,_=provider([HTTPError("https://api.sumup.com",404,"missing",{},io.BytesIO())])
 with pytest.raises(SumUpError) as caught:p.health()
 assert not caught.value.retryable and caught.value.diagnostic["category"]=="NOT_FOUND"
