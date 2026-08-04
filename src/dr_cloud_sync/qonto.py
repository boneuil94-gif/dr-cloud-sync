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
    def __init__(self, message: str, *, category="UNKNOWN", http_status=None, endpoint=None,
                 retryable=False, response_excerpt=None, duration_ms=None, stage="organization"):
        super().__init__(message); self.retryable=retryable; self.category=category
        self.http_status=http_status; self.endpoint=endpoint; self.sanitised_message=message
        self.response_excerpt=response_excerpt; self.duration_ms=duration_ms
        self.diagnostic={"category":category,"http_status":http_status,"endpoint_path":endpoint,
                         "stage":stage,"operation":"QONTO_HEALTH"}


class EnvironmentSecretProvider:
    """Resolve an opaque environment-variable reference without persisting it."""
    def __init__(self, environment, references=None): self.environment = environment; self.references=dict(references or {})
    def resolve(self, reference: str) -> str | None:
        if not reference: return None
        if reference.startswith("env:"):
            variable=reference.removeprefix("env:")
            return self.environment.get(variable) if variable else None
        variable=self.references.get(reference) or "DRCLOUD_SECRET_"+reference.upper().replace(".","_").replace("-","_")
        return self.environment.get(variable)
    def get(self, reference: str) -> str | None: return self.resolve(reference)


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
        if not value: raise QontoError("Configuration Qonto absente du runtime.",category="CONFIGURATION",stage="secret_resolution")
        return value if ":" in value or value.lower().startswith("bearer ") else f"Bearer {value}"
    def _get(self,path,params=None):
        url=f"{self.base_url}{path}"+("?"+urlencode(params,doseq=True) if params else "")
        request=Request(url,headers={"Authorization":self._authorization(),"Accept":"application/json"})
        started=time.monotonic()
        for attempt in range(self.retries):
            try:
                with self.opener(request,timeout=self.timeout) as response: return json.loads(response.read())
            except HTTPError as exc:
                duration=int((time.monotonic()-started)*1000)
                if exc.code in (401,403): raise QontoError("Authentification Qonto refusée",category="AUTH",http_status=exc.code,endpoint=path,duration_ms=duration,stage="authentication") from exc
                retryable=exc.code==429 or 500<=exc.code<600
                category="TIMEOUT" if exc.code==408 else "RATE_LIMIT" if exc.code==429 else "HTTP"
                if not retryable or attempt+1==self.retries: raise QontoError(f"Erreur HTTP Qonto ({exc.code})",category=category,http_status=exc.code,endpoint=path,retryable=retryable,duration_ms=duration) from exc
                delay=float(exc.headers.get("Retry-After") or min(30,2**attempt)); self.sleep(delay)
            except (URLError,TimeoutError) as exc:
                reason=getattr(exc,"reason",None); timeout=isinstance(exc,TimeoutError) or isinstance(reason,TimeoutError) or "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
                if attempt+1==self.retries: raise QontoError("Qonto network timeout" if timeout else "Connexion Qonto impossible",category="TIMEOUT" if timeout else "NETWORK",endpoint=path,retryable=True,duration_ms=int((time.monotonic()-started)*1000)) from exc
                self.sleep(min(30,2**attempt))
            except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise QontoError("Réponse Qonto invalide",category="INVALID_RESPONSE",endpoint=path,duration_ms=int((time.monotonic()-started)*1000),stage="response_validation") from exc
        raise AssertionError("unreachable")
    def _organization(self):
        payload=self._get("/v2/organization")
        if not isinstance(payload,dict) or not isinstance(payload.get("organization"),dict):
            raise QontoError("Structure de réponse Qonto inattendue",category="INVALID_RESPONSE",endpoint="/v2/organization",stage="response_validation")
        return payload["organization"]
    def health(self):
        if not self.configured:return {"status":"NOT_CONFIGURED"}
        self._organization();return {"status":"CONNECTED"}
    def accounts(self):
        def display_name(account):
            if account.get("name"): return str(account["name"])
            iban=str(account.get("iban") or "")
            return f"Compte Qonto ····{iban[-4:]}" if iban else f"Compte Qonto {account['id']}"
        return tuple(BankAccount(str(x["id"]),display_name(x),str(x.get("currency") or "EUR")) for x in self._organization().get("bank_accounts",[]))
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
