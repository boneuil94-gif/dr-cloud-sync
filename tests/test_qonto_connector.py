import io, json
from urllib.error import HTTPError, URLError
import pytest
from dr_cloud_sync.qonto import EnvironmentSecretProvider,QontoBankProvider,QontoError

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

def test_timeout_retries():
    p,_=provider([URLError("timeout")]*3)
    with pytest.raises(QontoError,match="network timeout"):p.health()

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
