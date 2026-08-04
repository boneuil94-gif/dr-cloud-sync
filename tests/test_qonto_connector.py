import io, json
from urllib.error import HTTPError, URLError
import pytest
from dr_cloud_sync.qonto import (EnvironmentSecretProvider,QONTO_USER_AGENT,QontoBankProvider,
    QontoError,cloudflare_1010,support_message)

class Response:
    def __init__(self,value):self.value=value
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self):return json.dumps(self.value).encode()

def provider(responses,credential="login:secret",sleep=lambda _:None):
    calls=[]
    def open(request,timeout): calls.append(request); value=responses.pop(0); return value if isinstance(value,Exception) is False else (_ for _ in ()).throw(value)
    return QontoBankProvider("QONTO",type("S",(),{"get":lambda self,ref:credential})(),opener=open,sleep=sleep),calls

def test_accounts_balances_health_and_secret_header():
    body={"organization":{"bank_accounts":[{"id":"a1","name":"Main","currency":"EUR","balance":"12.30","authorized_balance":"10"}]}}
    p,calls=provider([Response(body),Response(body),Response(body)])
    assert p.health()=={"status":"CONNECTED"};assert p.accounts()[0].account_id=="a1"
    assert p.balances()[0].available==10
    assert calls[0].headers["Authorization"]=="login:secret"

def test_transactions_real_shape_and_pagination():
    rows={"transactions":[{"transaction_id":"tx1","bank_account_id":"a1","settled_at":"2026-01-01T00:00:00Z","amount":"42.5","currency":"EUR","label":"CB","status":"completed","operation_type":"card"}],"meta":{"total_pages":2}}
    p,_=provider([Response(rows),Response({"transactions":[],"meta":{"total_pages":2}})])
    first=p.transactions(); assert first.next_cursor=="2" and first.transactions[0].status=="COMPLETED"
    assert p.transactions(first.next_cursor).next_cursor is None

@pytest.mark.parametrize("code,retryable",[(401,False),(403,False),(429,True),(500,True)])
def test_errors_are_sanitized(code,retryable):
    error=lambda:HTTPError("https://thirdparty.qonto.com/v2/organization",code,"x",{},io.BytesIO())
    p,_=provider([error(),error(),error()])
    with pytest.raises(QontoError) as caught:p.health()
    assert "secret" not in str(caught.value) and caught.value.retryable is retryable
    assert caught.value.category==("AUTH" if code in (401,403) else "RATE_LIMIT" if code==429 else "HTTP")
    assert caught.value.http_status==code and caught.value.endpoint=="/v2/organization"

def test_timeout_retries():
    p,_=provider([URLError("timeout")]*3)
    with pytest.raises(QontoError,match="network timeout"):p.health()

def test_dns_failure_and_invalid_json_are_distinct_and_safe():
    network,_=provider([URLError("name resolution failed")]*3)
    with pytest.raises(QontoError) as caught: network.health()
    assert caught.value.category=="NETWORK"
    class Invalid(Response):
        def read(self): return b"not-json"
    invalid,_=provider([Invalid({})])
    with pytest.raises(QontoError) as caught: invalid.health()
    assert caught.value.category=="INVALID_RESPONSE"

def test_unexpected_organization_shape_is_invalid_response():
    p,_=provider([Response({"unexpected":True})])
    with pytest.raises(QontoError) as caught:p.health()
    assert caught.value.category=="INVALID_RESPONSE"

def test_environment_secret_reference_is_resolved_without_exposure():
    secrets=EnvironmentSecretProvider({"QONTO_CREDENTIAL":"login:highly-sensitive"})
    assert secrets.resolve("env:QONTO_CREDENTIAL")=="login:highly-sensitive"
    assert secrets.resolve("env:") is None

def test_full_iban_is_not_persisted_as_account_name():
    iban="FR7612345678901234567890123"
    body={"organization":{"bank_accounts":[{"id":"a1","iban":iban,"currency":"EUR"}]}}
    p,_=provider([Response(body)])
    name=p.accounts()[0].name
    assert iban not in name and name.endswith(iban[-4:])

@pytest.mark.parametrize("body,content_type",[
    (b'<html><title>Attention Required! | Cloudflare</title><span class="error-code">Error 1010</span><p>owner has blocked access based on your browser\'s signature</p></html>',"text/html"),
    (b'{"error_code":1010,"provider":"cloudflare"}',"application/json"),
])
def test_cloudflare_1010_is_waf_and_never_retried(body,content_type):
    headers={"Server":"cloudflare","cf-ray":"abc123-CDG","Content-Type":content_type}
    error=HTTPError("https://thirdparty.qonto.com/v2/organization",403,"Forbidden",headers,io.BytesIO(body))
    p,calls=provider([error])
    with pytest.raises(QontoError) as caught:p.health()
    assert caught.value.category=="WAF" and caught.value.retryable is False
    assert caught.value.diagnostic["provider"]=="CLOUDFLARE"
    assert caught.value.diagnostic["cloudflare_code"]==1010
    assert caught.value.diagnostic["cf_ray"]=="abc123-CDG"
    assert len(calls)==1 and "login:secret" not in str(caught.value.diagnostic)

def test_qonto_json_403_is_auth_not_waf():
    headers={"Content-Type":"application/json"}
    error=HTTPError("https://thirdparty.qonto.com/v2/organization",403,"Forbidden",headers,io.BytesIO(b'{"message":"forbidden"}'))
    p,_=provider([error])
    with pytest.raises(QontoError) as caught:p.health()
    assert caught.value.category=="AUTH"

def test_api_headers_are_stable_and_not_browser_impersonation():
    p,calls=provider([Response({"organization":{"bank_accounts":[]}})])
    p.health(); headers={k.lower():v for k,v in calls[0].header_items()}
    assert headers["user-agent"]==QONTO_USER_AGENT and headers["accept"]=="application/json"
    assert headers["authorization"]=="login:secret"
    assert not ({"cookie","referer","sec-ch-ua","sec-fetch-site"}&headers.keys())

def test_support_message_is_sanitised():
    message=support_message(timestamp_utc="2026-08-04T12:00:00Z",cf_ray="ray-CDG",egress_ip="192.0.2.10")
    assert "GET /v2/organization" in message and "192.0.2.10" in message and QONTO_USER_AGENT in message
    assert "login:secret" not in message and "Authorization:" not in message

def test_cloudflare_classifier_requires_cloudflare_evidence():
    assert cloudflare_1010(403,{"Server":"origin"},'{"error_code":1010}') is None
