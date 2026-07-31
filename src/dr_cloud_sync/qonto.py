"""Read-only Qonto Business API adapter (v2 organization and transactions)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json, time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .bank import BankAccount, BankBalance, BankTransaction, TransactionPage


class QontoError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message); self.retryable = retryable


class EnvironmentSecretProvider:
    """Resolve an opaque environment-variable reference without persisting it."""
    def __init__(self, environment): self.environment = environment
    def get(self, reference: str) -> str | None: return self.environment.get(reference) if reference else None


class QontoBankProvider:
    BASE_URL = "https://thirdparty.qonto.com"
    def __init__(self, credential_reference: str, secrets, *, opener=urlopen, timeout=8,
                 page_size=100, retries=3, base_url=None, sleep=time.sleep):
        self._reference=credential_reference; self._secrets=secrets; self.opener=opener
        self.timeout=timeout; self.page_size=page_size; self.retries=retries
        self.base_url=(base_url or self.BASE_URL).rstrip("/"); self.sleep=sleep
    @property
    def configured(self): return bool(self._reference and self._secrets.get(self._reference))
    def _authorization(self):
        value=self._secrets.get(self._reference)
        if not value: raise QontoError("Qonto credentials are not configured")
        return value if ":" in value or value.lower().startswith("bearer ") else f"Bearer {value}"
    def _get(self,path,params=None):
        url=f"{self.base_url}{path}"+("?"+urlencode(params,doseq=True) if params else "")
        request=Request(url,headers={"Authorization":self._authorization(),"Accept":"application/json"})
        for attempt in range(self.retries):
            try:
                with self.opener(request,timeout=self.timeout) as response: return json.loads(response.read())
            except HTTPError as exc:
                if exc.code in (401,403): raise QontoError(f"Qonto authentication failed (HTTP {exc.code})") from exc
                retryable=exc.code==429 or 500<=exc.code<600
                if not retryable or attempt+1==self.retries: raise QontoError(f"Qonto HTTP {exc.code}",retryable=retryable) from exc
                delay=float(exc.headers.get("Retry-After") or min(30,2**attempt)); self.sleep(delay)
            except (URLError,TimeoutError) as exc:
                if attempt+1==self.retries: raise QontoError("Qonto network timeout",retryable=True) from exc
                self.sleep(min(30,2**attempt))
            except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise QontoError("Qonto returned invalid JSON") from exc
        raise AssertionError("unreachable")
    def _organization(self): return self._get("/v2/organization").get("organization",{})
    def health(self):
        if not self.configured:return {"status":"NOT_CONFIGURED"}
        self._organization();return {"status":"CONNECTED"}
    def accounts(self):
        return tuple(BankAccount(str(x["id"]),str(x.get("name") or x.get("iban") or x["id"]),str(x.get("currency") or "EUR")) for x in self._organization().get("bank_accounts",[]))
    def balances(self):
        observed=datetime.now(timezone.utc).isoformat(); result=[]
        for x in self._organization().get("bank_accounts",[]):
            result.append(BankBalance(str(x["id"]),Decimal(str(x.get("balance",0))),str(x.get("currency") or "EUR"),observed,Decimal(str(x["authorized_balance"])) if x.get("authorized_balance") is not None else None))
        return tuple(result)
    def transactions(self,cursor=None):
        # Qonto v2 uses page-based pagination. The opaque Data Hub cursor is the next page.
        page=int(cursor or 1); payload=self._get("/v2/transactions",{"current_page":page,"per_page":self.page_size})
        rows=payload.get("transactions",[]); meta=payload.get("meta",{})
        result=[]
        for x in rows:
            result.append(BankTransaction(str(x.get("bank_account_id") or x.get("bank_account",{}).get("id") or "unknown"),str(x.get("settled_at") or x.get("emitted_at") or x.get("updated_at")),Decimal(str(x.get("amount",0))),str(x.get("currency") or "EUR"),str(x.get("label") or x.get("note") or "Transaction Qonto"),str(x["transaction_id"]),str(x.get("emitted_at") or "") or None,str(x.get("counterparty_name") or "") or None,str(x.get("reference") or "") or None,str(x.get("status") or "pending").upper(),raw_metadata={"operation_type":x.get("operation_type"),"side":x.get("side")}))
        total=int(meta.get("total_pages") or page); next_cursor=str(page+1) if page<total else None
        return TransactionPage(tuple(result),next_cursor)
