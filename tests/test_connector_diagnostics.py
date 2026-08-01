import io
import json
from urllib.error import HTTPError, URLError

import pytest

from dr_cloud_sync.connector_diagnostics import DiagnosticRepository, from_exception, safe_path, sanitize
from dr_cloud_sync.prestashop import PrestaShopClient, PrestaShopError
from dr_cloud_sync.shopcaisse import ShopCaisseClient, ShopCaisseError


@pytest.mark.parametrize("value",[
    "Authorization: Bearer very.secret.token", "Basic dXNlcjpwYXNzd29yZA==",
    "api_key=private", "password=hunter2", "Cookie: session=private",
    '{"token":"private","nested":{"secret":"hidden"}}',
])
def test_redaction_removes_sensitive_values(value):
    result=sanitize(value)
    assert "very.secret.token" not in result and "dXNlcjpwYXNzd29yZA" not in result
    assert "private" not in result and "hunter2" not in result and "hidden" not in result


def test_path_drops_host_and_redacts_sensitive_query():
    assert safe_path("https://internal.example/orders?page=2&api_key=secret") == "/orders?page=2&api_key=%5BREDACTED%5D"


@pytest.mark.parametrize("status",[401,403,404,429,500])
def test_shopcaisse_http_diagnostic(status):
    def opener(*_args,**_kwargs):
        raise HTTPError("https://host/authentication?api_key=no",status,"failure",{},io.BytesIO(b'{"password":"no","message":"denied"}'))
    with pytest.raises(ShopCaisseError) as caught:
        ShopCaisseClient("secret",opener=opener,retries=1).health()
    item=from_exception(source_id="shopcaisse_sales",provider="ShopCaisse",operation="authentication",stage="authentication",exc=caught.value)
    assert item.http_status==status and item.category == ("AUTH" if status in (401,403) else "HTTP")
    assert "secret" not in json.dumps(item.__dict__) and "password" in item.response_excerpt


def test_shopcaisse_timeout_and_invalid_json():
    def timeout(*_a,**_k): raise TimeoutError()
    with pytest.raises(ShopCaisseError) as caught: ShopCaisseClient("key",opener=timeout,retries=1).health()
    assert from_exception(source_id="s",provider="ShopCaisse",operation="authentication",stage="authentication",exc=caught.value).category=="TIMEOUT"
    class Response:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def read(self): return b"not-json"
    with pytest.raises(ShopCaisseError) as caught: ShopCaisseClient("key",opener=lambda *_a,**_k:Response()).health()
    assert caught.value.diagnostic["category"]=="PARSING"


def test_prestashop_stages_empty_invalid_and_pagination():
    calls=[]
    class Response:
        def __init__(self,payload): self.payload=payload
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def read(self): return self.payload
    payloads=[b'{"orders":[{"id":1}]}',b'{"orders":[]}']
    client=PrestaShopClient("https://example.test/api","key",page_size=1,opener=lambda request,**_k:(calls.append(request.full_url) or Response(payloads.pop(0))))
    assert [x["id"] for x in client.iter_resource("orders")]==[1] and len(calls)==2
    assert list(PrestaShopClient("https://x/api","key",opener=lambda *_a,**_k:Response(b'{"order_states":[]}')).iter_resource("order_states"))==[]
    with pytest.raises(PrestaShopError) as caught: list(PrestaShopClient("https://x/api","key",opener=lambda *_a,**_k:Response(b"invalid")).iter_resource("order_details"))
    assert caught.value.diagnostic["stage"]=="parsing"


def test_repository_keeps_structured_limited_history(tmp_path):
    repo=DiagnosticRepository(tmp_path/"shared.sqlite")
    error=ShopCaisseError("Bearer forbidden",diagnostic={"category":"AUTH","http_status":401,"endpoint_path":"https://secret.host/authentication?token=x","response_excerpt":{"api_key":"x","error":"denied"}})
    identifier=repo.add(from_exception(source_id="shopcaisse_sales",provider="ShopCaisse",operation="authentication",stage="authentication",exc=error,job_id="sync_shopcaisse_sales",run_id=7,request_id="req-1"))
    row=repo.recent("shopcaisse_sales",1)[0]
    assert identifier and row["run_id"]==7 and row["endpoint_path"]=="/authentication?token=%5BREDACTED%5D"
    assert "x" not in row["response_excerpt"]


def test_repository_marks_failure_historical_after_later_successful_run(tmp_path):
    repo=DiagnosticRepository(tmp_path/"shared.sqlite")
    with repo.connect() as db:
        db.executescript("""
        CREATE TABLE data_sources(source_id TEXT PRIMARY KEY,last_success_at TEXT);
        CREATE TABLE data_hub_sync_runs(run_id INTEGER PRIMARY KEY,job_id TEXT,started_at TEXT,completed_at TEXT,status TEXT,attempt INTEGER,result_json TEXT,error TEXT);
        INSERT INTO data_sources VALUES('prestashop_sales','2026-08-01T11:00:00+00:00');
        INSERT INTO data_hub_sync_runs VALUES(7,'sync_prestashop_sales','2026-08-01T10:00:00+00:00','2026-08-01T10:01:00+00:00','FAILED',1,NULL,'old');
        INSERT INTO data_hub_sync_runs VALUES(8,'sync_prestashop_sales','2026-08-01T11:00:00+00:00','2026-08-01T11:01:00+00:00','SUCCEEDED',2,'{}',NULL);
        """)
    item=from_exception(source_id="prestashop_sales",provider="PrestaShop",operation="sales",stage="execution",exc=ValueError("sold_at must include a UTC offset"),job_id="sync_prestashop_sales",run_id=7)
    object.__setattr__(item,"occurred_at","2026-08-01T10:01:00+00:00")
    repo.add(item)
    row=repo.recent("prestashop_sales",1)[0]
    assert row["historical"] is True and row["current"] is False
