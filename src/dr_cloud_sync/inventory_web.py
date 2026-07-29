"""Dependency-free WSGI API and server for the local inventory UI."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
from .inventory import InventoryError, InventoryRepository, InventoryService

ROOT = Path(__file__).parent / "static"

class InventoryApp:
    def __init__(self, service: InventoryService, report_output: Path | None = None):
        self.service = service; self.report_output = report_output
    def __call__(self, environ, start_response):
        path=environ.get("PATH_INFO", "/"); method=environ.get("REQUEST_METHOD", "GET")
        try:
            if path == "/": return self._send(start_response, (ROOT/"inventory.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/inventory.js": return self._send(start_response, (ROOT/"inventory.js").read_bytes(), "text/javascript; charset=utf-8")
            if path == "/inventory.css": return self._send(start_response, (ROOT/"inventory.css").read_bytes(), "text/css; charset=utf-8")
            if path == "/api/state": return self._json(start_response, {"session":self.service.session(),"progress":self.service.progress()})
            if path == "/api/items":
                q=parse_qs(environ.get("QUERY_STRING", "")); return self._json(start_response, self.service.search(q.get("q",[""])[0],q.get("view",["ALL"])[0],q.get("without_ean",["0"])[0]=="1"))
            if path == "/api/scan": return self._json(start_response, self.service.scan(parse_qs(environ.get("QUERY_STRING", "")).get("ean",[""])[0]))
            if path == "/api/count" and method == "POST":
                data=self._body(environ); return self._json(start_response,self.service.count(data["prestashop_key"],data.get("physical_quantity"),data.get("source","MANUAL"),data.get("action","COUNT")))
            if path == "/api/history": return self._json(start_response,self.service.repo.history(self.service.session()["id"]))
            if path == "/api/complete" and method == "POST": return self._json(start_response,self.service.complete())
            if path == "/api/report": return self._json(start_response,self.service.report(self.report_output))
            if path == "/api/export.csv": return self._send(start_response,self.service.csv().encode(),"text/csv; charset=utf-8",headers=[("Content-Disposition","attachment; filename=inventaire-drcloud.csv")])
            return self._json(start_response,{"error":"Introuvable"},"404 Not Found")
        except (InventoryError, KeyError, json.JSONDecodeError) as exc:
            return self._json(start_response,{"error":str(exc)},"400 Bad Request")
    @staticmethod
    def _body(env): return json.loads(env["wsgi.input"].read(int(env.get("CONTENT_LENGTH") or 0)) or b"{}")
    @staticmethod
    def _send(start,body,kind,status="200 OK",headers=None): start(status,[("Content-Type",kind),("Cache-Control","no-store"),*(headers or [])]); return [body]
    def _json(self,start,value,status="200 OK"): return self._send(start,json.dumps(value,ensure_ascii=False).encode(),"application/json; charset=utf-8",status)

def serve(catalogue: Path, validation: Path, database: Path, host="127.0.0.1", port=8080):
    service=InventoryService(catalogue,validation,InventoryRepository(database))
    print(f"Inventaire DRCloud: http://{host}:{port}")
    make_server(host,port,InventoryApp(service,Path("dist/rapport-inventaire-drcloud.json"))).serve_forever()
