"""WSGI application for the unified, authenticated DrCloud OS interface."""
from __future__ import annotations
from dataclasses import asdict
import hashlib, hmac, json, logging, os, secrets, time, uuid
from pathlib import Path
from urllib.parse import parse_qs
from .connectors import DisabledConnector
from .domain import Product
from .inventory import InventoryError, InventoryRepository, InventoryService
from .os_config import OSSettings
from .repositories import SQLiteOSRepository
from .roadmap import DEFAULT_ROADMAP, RoadmapService
from .services import AssignBarcodeService, BarcodeError
from .admin_status import AdminStatusService, application_metadata
from .modules import available_pages, render_navigation

ROOT = Path(__file__).parent / "static"
LOG = logging.getLogger("drcloud.os")

# One declaration drives the shared shell. Adding a real module only requires a
# route, a content template and an entry here; pages never duplicate navigation.
PAGES = available_pages()

class InventoryApp:
    def __init__(self, service: InventoryService, report_output: Path | None = None, os_repository=None,
                 roadmap_service: RoadmapService | None = None, settings: OSSettings | None = None):
        self.service=service; self.report_output=report_output; self.settings=settings
        products=[Product(i["drcloud_product_key"],i["prestashop_key"],i.get("product_id"),i.get("combination_id"),i["shopcaisse_item_id"],service._name(i),service._ean(i),None,i.get("stock_prestashop"),i.get("stock_shopcaisse")) for i in service.items]
        self.os_repository=os_repository or SQLiteOSRepository(service.repo.path,products)
        self.barcodes=AssignBarcodeService(self.os_repository,self.os_repository,DisabledConnector(),DisabledConnector())
        self.roadmap_service=roadmap_service or RoadmapService(DEFAULT_ROADMAP); self.failures={}
        self.admin_status=AdminStatusService(service.repo.path)

    def __call__(self, env, start):
        request_id=env.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4()); path=env.get("PATH_INFO", "/"); method=env.get("REQUEST_METHOD", "GET")
        try:
            if path == "/health":
                try: self.service.repo.db.execute("SELECT 1"); database="ok"; status="ok"
                except Exception: database="error"; status="degraded"
                return self._json(start,{"status":status,"application":"drcloud-os",**application_metadata(),"database":database}, headers=[("X-Request-ID",request_id)])
            if path in {"/manifest.webmanifest","/icon.svg","/drcloud-logo.png","/inventory.css","/inventory.js","/roadmap.js","/dashboard.js","/administration.js"}:
                file={"/manifest.webmanifest":"manifest.webmanifest"}.get(path,path[1:]); kind="application/manifest+json" if path.endswith("webmanifest") else "image/svg+xml" if path.endswith("svg") else "text/css; charset=utf-8" if path.endswith("css") else "text/javascript; charset=utf-8"
                if path.endswith(".png"): kind="image/png"
                return self._send(start,(ROOT/file).read_bytes(),kind,headers=[("X-Request-ID",request_id)])
            session=self._session(env)
            if path == "/login": return self._login(env,start,method,session,request_id)
            if not session: return self._redirect(start,"/login",request_id)
            if path == "/logout" and method == "POST":
                self._csrf(env,session); return self._redirect(start,"/login",request_id,clear=True)
            if method not in {"GET","HEAD","OPTIONS"}: self._csrf(env,session)
            if path == "/": return self._html(start,"dashboard.html",session,request_id)
            if path == "/catalogue": return self._html(start,"catalogue.html",session,request_id)
            if path == "/inventaire": return self._html(start,"inventory.html",session,request_id)
            if path == "/roadmap": return self._html(start,"roadmap.html",session,request_id)
            if path == "/administration": return self._html(start,"administration.html",session,request_id)
            if path == "/api/dashboard":
                road=self.roadmap_service.load(); return self._json(start,{"progress_percent":road["global_progress_percent"],"next":next((m["next"] for m in road["modules"] if m.get("next")),None),"catalogue":len(self.service.items),"inventory":{"session":self.service.session(),"progress":self.service.progress()},"systems":self.admin_status.collect()},headers=[("X-Request-ID",request_id)])
            if path == "/api/state": return self._json(start,{"session":self.service.session(),"progress":self.service.progress(),"proposal":self.service.proposal()})
            if path == "/api/roadmap": return self._json(start,self.roadmap_service.load())
            if path == "/api/admin/status": return self._json(start,self.admin_status.collect(),headers=[("X-Request-ID",request_id)])
            if path == "/api/catalogue": return self._json(start,self._catalogue(parse_qs(env.get("QUERY_STRING", ""))))
            if path == "/api/items":
                q=parse_qs(env.get("QUERY_STRING", "")); return self._json(start,self.service.search(q.get("q",[""])[0],q.get("view",["ALL"])[0],q.get("without_ean",["0"])[0]=="1"))
            if path == "/api/scan": return self._json(start,self.service.scan(parse_qs(env.get("QUERY_STRING", "")).get("ean",[""])[0]))
            if path == "/api/count" and method == "POST":
                data=self._body(env); return self._json(start,self.service.count(data["prestashop_key"],data.get("physical_quantity"),data.get("source","MANUAL"),data.get("action","COUNT")))
            if path == "/api/barcodes/propose" and method == "POST":
                data=self._body(env); return self._json(start,asdict(self.barcodes.propose(data["drcloud_product_key"],data["ean"])))
            if path == "/api/barcodes/confirm" and method == "POST": return self._json(start,asdict(self.barcodes.confirm(self._body(env)["id"])))
            if path == "/api/history": return self._json(start,self.service.repo.history(self.service.session()["id"]))
            if path == "/api/complete" and method == "POST": return self._json(start,self.service.complete())
            if path == "/api/inventory/session" and method == "POST": return self._json(start,self.service.new_session())
            if path == "/api/inventory/proposal": return self._json(start,self.service.proposal())
            if path == "/api/inventory/proposal/validate" and method == "POST": return self._json(start,self.service.validate(session.get("u") or "authenticated"))
            if path == "/api/inventory/proposal/apply" and method == "POST": return self._json(start,self.service.apply(session.get("u") or "authenticated"))
            if path == "/api/inventory/proposal/validate-and-apply" and method == "POST":
                self.service.validate(session.get("u") or "authenticated")
                return self._json(start,self.service.apply(session.get("u") or "authenticated"))
            if path == "/api/report": return self._json(start,self.service.report(self.report_output))
            if path == "/api/export.csv": return self._send(start,self.service.csv().encode(),"text/csv; charset=utf-8",headers=[("Content-Disposition","attachment; filename=inventaire-drcloud.csv")])
            return self._error(start,404,request_id)
        except PermissionError: return self._error(start,403,request_id)
        except (InventoryError,BarcodeError,KeyError,json.JSONDecodeError) as exc: return self._json(start,{"error":str(exc)},"400 Bad Request")
        except Exception:
            LOG.exception("request_failed request_id=%s path=%s",request_id,path); return self._error(start,500,request_id)

    def _login(self,env,start,method,session,request_id):
        if not self.settings: return self._error(start,401,request_id)
        if method=="GET": return self._html(start,"login.html",None,request_id)
        remote=env.get("REMOTE_ADDR",""); now=time.monotonic(); attempts=[x for x in self.failures.get(remote,[]) if now-x<300]; self.failures[remote]=attempts
        if len(attempts)>=5: return self._error(start,429,request_id)
        data=parse_qs(self._raw_body(env).decode("utf-8")); user=data.get("username",[""])[0]; password=data.get("password",[""])[0]
        if not (hmac.compare_digest(user,self.settings.admin_username) and hmac.compare_digest(password,self.settings.admin_password)):
            attempts.append(now); LOG.warning("login_failed request_id=%s remote=%s",request_id,remote); return self._html(start,"login.html",None,request_id,status="401 Unauthorized")
        self.failures.pop(remote,None); token={"u":user,"exp":int(time.time())+28800,"csrf":secrets.token_urlsafe(24)}; cookie=self._encode(token)
        return self._redirect(start,"/",request_id,cookie=cookie)

    def _session(self,env):
        if not self.settings: return {"u":"legacy-test","csrf":"test"}
        cookies=dict(x.strip().split("=",1) for x in env.get("HTTP_COOKIE","").split(";") if "=" in x)
        raw=cookies.get("drcloud_session");
        if not raw: return None
        try:
            payload,sig=raw.rsplit(".",1); expected=hmac.new(self.settings.secret_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig,expected): return None
            data=json.loads(bytes.fromhex(payload)); return data if data["exp"]>time.time() else None
        except (ValueError,KeyError,json.JSONDecodeError): return None
    def _encode(self,data):
        payload=json.dumps(data,separators=(",",":")).encode().hex(); return payload+"."+hmac.new(self.settings.secret_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
    def _csrf(self,env,session):
        if not self.settings: return
        token=env.get("HTTP_X_CSRF_TOKEN") or parse_qs(self._raw_body(env).decode(errors="ignore")).get("csrf_token",[""])[0]
        if not hmac.compare_digest(token,session["csrf"]): raise PermissionError("CSRF")
    @staticmethod
    def _raw_body(env):
        if "drcloud.raw_body" not in env: env["drcloud.raw_body"]=env["wsgi.input"].read(min(int(env.get("CONTENT_LENGTH") or 0),1_048_576))
        return env["drcloud.raw_body"]
    def _body(self,env): return json.loads(self._raw_body(env) or b"{}")
    def _html(self,start,name,session,request_id,status="200 OK"):
        content_name="inventory.html" if name == "catalogue.html" else name
        html=(ROOT/content_name).read_text(encoding="utf-8"); safe=self.settings.safe_mode if self.settings else True
        if name in PAGES:
            module = PAGES[name]
            title, active, script = module.label, module.id, module.script
            shell=(ROOT/"app-shell.html").read_text(encoding="utf-8")
            html=shell.replace("{{PAGE_CONTENT}}",html).replace("{{PAGE_TITLE}}",title).replace("{{PAGE_SCRIPT}}",script)
            html=html.replace("{{NAVIGATION}}", render_navigation(active))
        html=html.replace("{{SAFE_BANNER}}",'<div class="safe">Mode sécurisé — écritures externes désactivées</div>' if safe else "").replace("{{CSRF}}",session["csrf"] if session else "")
        return self._send(start,html.encode(),"text/html; charset=utf-8",status,[("X-Request-ID",request_id)])
    def _catalogue(self,query):
        text=query.get("q",[""])[0].casefold(); selected=query.get("filter",["ALL"])[0]; conflicts={p.drcloud_product_key for p in self.os_repository.all() for other in self.os_repository.by_ean(p.ean) if p.ean and other.drcloud_product_key != p.drcloud_product_key}; counts=self.service.repo.counts(self.service.session()["id"]); rows=[]
        for p in self.os_repository.all():
            if text and text not in f"{p.name} {p.ean}".casefold(): continue
            if (selected=="WITH_EAN" and not p.ean) or (selected=="WITHOUT_EAN" and p.ean) or (selected=="CONFLICT" and p.drcloud_product_key not in conflicts): continue
            row=asdict(p); row["ean_status"]="CONFLICT" if p.drcloud_product_key in conflicts else "WITH_EAN" if p.ean else "WITHOUT_EAN"; count=counts.get(p.prestashop_key); row["physical_quantity"]=count["physical_quantity"] if count else None; rows.append(row)
        return rows
    def _error(self,start,code,request_id): return self._html(start,"error.html",{"csrf":""},request_id,status={401:"401 Unauthorized",403:"403 Forbidden",404:"404 Not Found",429:"429 Too Many Requests",500:"500 Internal Server Error"}[code])
    def _redirect(self,start,location,request_id,cookie=None,clear=False):
        headers=[("Location",location),("X-Request-ID",request_id)]; secure=self.settings and self.settings.environment=="production"; attrs="; Path=/; HttpOnly; SameSite=Lax"+("; Secure" if secure else "")
        if cookie: headers.append(("Set-Cookie",f"drcloud_session={cookie}{attrs}; Max-Age=28800"))
        if clear: headers.append(("Set-Cookie",f"drcloud_session={attrs}; Max-Age=0"))
        return self._send(start,b"","text/plain","303 See Other",headers)
    @staticmethod
    def _send(start,body,kind,status="200 OK",headers=None):
        security=[("Content-Type",kind),("Cache-Control","no-store"),("Content-Security-Policy","default-src 'self'; img-src 'self' data:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"),("X-Content-Type-Options","nosniff"),("Referrer-Policy","no-referrer"),("X-Frame-Options","DENY")]
        start(status,security+(headers or [])); return [body]
    def _json(self,start,value,status="200 OK",headers=None): return self._send(start,json.dumps(value,ensure_ascii=False,default=str).encode(),"application/json; charset=utf-8",status,headers)

def create_app(settings: OSSettings | None=None):
    settings=settings or OSSettings.from_env(); settings.data_dir.mkdir(parents=True,exist_ok=True)
    catalogue=Path(os.environ.get("INVENTORY_CATALOGUE",settings.data_dir/"catalogue.json")); report=Path(os.environ.get("INVENTORY_MAPPING_REPORT",settings.data_dir/"catalogue-report.json"))
    return InventoryApp(InventoryService(catalogue,report,InventoryRepository(settings.database)),settings.data_dir/"rapport-inventaire.json",settings=settings)

def serve(catalogue:Path,validation:Path,database:Path,host="127.0.0.1",port=8080):
    from waitress import serve as waitress_serve
    service=InventoryService(catalogue,validation,InventoryRepository(database)); waitress_serve(InventoryApp(service),host=host,port=port)
