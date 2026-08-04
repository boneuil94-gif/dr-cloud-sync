"""Read-only, secret-safe Qonto edge diagnostic run from the application container."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .qonto import QONTO_USER_AGENT, cloudflare_1010

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
        return {"test":label,"http":None,"server":None,"cf_ray":None,"cloudflare_code":None,
                "content_type":None,"duration_ms":int((time.monotonic()-started)*1000)}
    waf=cloudflare_1010(status,response_headers,body)
    return {"test":label,"http":status,"server":response_headers.get("Server"),
            "cf_ray":response_headers.get("cf-ray"),"cloudflare_code":waf and 1010,
            "content_type":response_headers.get("Content-Type"),
            "duration_ms":int((time.monotonic()-started)*1000)}


def run(credential=None):
    result={"endpoint":URL,"timestamp_utc":datetime.now(timezone.utc).isoformat(),"user_agent":QONTO_USER_AGENT}
    try: socket.getaddrinfo(HOST,443); result["dns"]="OK"
    except OSError: result["dns"]="NON"
    started=time.monotonic()
    try:
        with socket.create_connection((HOST,443),timeout=8) as raw:
            with ssl.create_default_context().wrap_socket(raw,server_hostname=HOST): pass
        result["tls"]="OK"
    except OSError: result["tls"]="NON"
    result["tls_duration_ms"]=int((time.monotonic()-started)*1000)
    result["requests"]=[_request("sans_authorization")]
    if credential:
        result["requests"].extend((_request("auth_user_agent_applicatif",credential),
                                   _request("auth_client_actuel",credential,QONTO_USER_AGENT)))
    else:
        result["requests"].append({"test":"requêtes_authentifiées","status":"NON_EXECUTEES_CREDENTIAL_ABSENT"})
    return result


def main():
    # The credential is read only from the process environment and is never returned or logged.
    print(json.dumps(run(os.environ.get("QONTO_CREDENTIAL")),ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
