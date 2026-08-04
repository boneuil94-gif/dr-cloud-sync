"""Read-only, secret-safe Qonto edge diagnostic run from the application container."""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .qonto import (EnvironmentSecretProvider, QONTO_USER_AGENT, QontoBankProvider,
                    QontoError, cloudflare_1010, credential_is_valid, credential_structure)

HOST = "thirdparty.qonto.com"
URL = f"https://{HOST}/v2/organization"


def _request(label, authorization=None, user_agent=QONTO_USER_AGENT, opener=urlopen):
    headers = {"Accept":"application/json", "User-Agent":user_agent}
    if authorization: headers["Authorization"] = authorization
    started = time.monotonic()
    try:
        with opener(Request(URL, headers=headers), timeout=8) as response:
            body=response.read(65536); status=getattr(response,"status",200); response_headers=response.headers
    except HTTPError as exc:
        body=exc.read(65536); status=exc.code; response_headers=exc.headers
    except (URLError, TimeoutError, OSError):
        return {"test":label,"http":None,"content_type":None,
                "duration_ms":int((time.monotonic()-started)*1000),"category":"NETWORK",
                "request_id":None,"response_sanitized":"Connexion impossible"}
    waf=cloudflare_1010(status,response_headers,body)
    category="WAF" if waf else "AUTH" if status in (401,403) else "OK" if status == 200 else "HTTP"
    return {"test":label,"http":status,
            "content_type":response_headers.get("Content-Type"),
            "duration_ms":int((time.monotonic()-started)*1000),"category":category,
            "request_id":response_headers.get("x-request-id") or response_headers.get("request-id"),
            "response_sanitized":"Réponse reçue"}


def _provider_request(credential, opener=urlopen):
    """Execute the actual provider health path and expose only allow-listed facts."""
    started=time.monotonic(); captured={}
    def recording_open(request, timeout):
        try: response=opener(request,timeout=timeout)
        except HTTPError as exc:
            captured.update(status=exc.code,headers=exc.headers); raise
        captured.update(status=getattr(response,"status",200),headers=response.headers); return response
    provider=QontoBankProvider("env:QONTO_CREDENTIAL",
        EnvironmentSecretProvider({"QONTO_CREDENTIAL":credential}),opener=recording_open)
    try:
        provider.health(); category="OK"
    except QontoError as exc:
        category=exc.category; captured.setdefault("status",exc.http_status)
    headers=captured.get("headers") or {}
    return {"test":"A_client_applicatif","http":captured.get("status"),
        "content_type":headers.get("Content-Type"),"duration_ms":int((time.monotonic()-started)*1000),
        "category":category,"request_id":headers.get("x-request-id") or headers.get("request-id"),
        "response_sanitized":"Réponse reçue" if captured else "Aucune réponse HTTP"}


def run(credential=None, *, reference=None, opener=urlopen, network_checks=True):
    facts=credential_structure(credential)
    result={"credential_ref_present":bool(reference),"reference_resolved":credential is not None,
            **facts,"format_structurally_valid":credential_is_valid(credential),
            "authorization_sent":bool(credential and credential_is_valid(credential)),
            "method":"API key organisation","basic_base64_bearer":False,
            "api_environment":"Production"}
    if not network_checks:
        result["requests"]=[]; result["classification"]="FORMAT_INVALID" if not credential_is_valid(credential) else "NOT_EXECUTED"; return result
    if credential_is_valid(credential):
        result["requests"]=[_provider_request(credential,opener),
                            _request("B_requete_minimale",credential,QONTO_USER_AGENT,opener)]
        statuses=[item["http"] for item in result["requests"]]
        result["classification"]=("CONNECTED" if statuses == [200,200] else
            "CLIENT_BUG" if statuses == [401,200] else
            "CREDENTIAL_REJECTED" if statuses == [401,401] else "OTHER")
    else:
        result["requests"]=[{"test":"A/B","status":"NON_EXECUTEES_FORMAT_INVALID"}]
        result["classification"]="FORMAT_INVALID"
    return result


def main():
    # The credential is read only from the process environment and is never returned or logged.
    reference=os.environ.get("QONTO_CREDENTIAL_REF")
    credential=EnvironmentSecretProvider(os.environ).resolve(reference or "")
    print(json.dumps(run(credential,reference=reference),ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
