"""Dependency-free WSGI API for the unified DrCloud OS interface."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
from .connectors import DisabledConnector
from .domain import Product
from .inventory import InventoryError, InventoryRepository, InventoryService
from .repositories import SQLiteOSRepository
from .services import AssignBarcodeService, BarcodeError

ROOT = Path(__file__).parent / "static"

class InventoryApp:
    def __init__(self, service: InventoryService, report_output: Path | None = None, os_repository=None):
        self.service = service; self.report_output = report_output
        products=[Product(i["drcloud_product_key"],i["prestashop_key"],i.get("product_id"),i.get("combination_id"),i["shopcaisse_item_id"],service._name(i),service._ean(i),None,i.get("stock_prestashop"),i.get("stock_shopcaisse")) for i in service.items]
        self.os_repository=os_repository or SQLiteOSRepository(service.repo.path,products)
        self.barcodes=AssignBarcodeService(self.os_repository,self.os_repository,DisabledConnector(),DisabledConnector())
    def __call__(self, environ, start_response):
        path=environ.get("PATH_INFO", "/"); method=environ.get("REQUEST_METHOD", "GET")
        try:
            if path in {"/", "/catalogue"}: return self._send(start_response, (ROOT/"inventory.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/inventory.js": return self._send(start_response, (ROOT/"inventory.js").read_bytes(), "text/javascript; charset=utf-8")
            if path == "/inventory.css": return self._send(start_response, (ROOT/"inventory.css").read_bytes(), "text/css; charset=utf-8")
            if path == "/api/state": return self._json(start_response, {"session":self.service.session(),"progress":self.service.progress()})
            if path == "/api/catalogue": return self._json(start_response,self._catalogue(parse_qs(environ.get("QUERY_STRING", ""))))
            if path == "/api/items":
                q=parse_qs(environ.get("QUERY_STRING", "")); return self._json(start_response, self.service.search(q.get("q",[""])[0],q.get("view",["ALL"])[0],q.get("without_ean",["0"])[0]=="1"))
            if path == "/api/scan": return self._json(start_response, self.service.scan(parse_qs(environ.get("QUERY_STRING", "")).get("ean",[""])[0]))
            if path == "/api/count" and method == "POST":
                data=self._body(environ); result=self.service.count(data["prestashop_key"],data.get("physical_quantity"),data.get("source","MANUAL"),data.get("action","COUNT")); return self._json(start_response,result)
            if path == "/api/barcodes/propose" and method == "POST":
                data=self._body(environ); return self._json(start_response,asdict(self.barcodes.propose(data["drcloud_product_key"],data["ean"])))
            if path == "/api/barcodes/confirm" and method == "POST":
                return self._json(start_response,asdict(self.barcodes.confirm(self._body(environ)["id"])))
            if path == "/api/history": return self._json(start_response,self.service.repo.history(self.service.session()["id"]))
            if path == "/api/complete" and method == "POST": return self._json(start_response,self.service.complete())
            if path == "/api/report": return self._json(start_response,self.service.report(self.report_output))
            if path == "/api/export.csv": return self._send(start_response,self.service.csv().encode(),"text/csv; charset=utf-8",headers=[("Content-Disposition","attachment; filename=inventaire-drcloud.csv")])
            return self._json(start_response,{"error":"Introuvable"},"404 Not Found")
        except (InventoryError, BarcodeError, KeyError, json.JSONDecodeError) as exc:
            return self._json(start_response,{"error":str(exc)},"400 Bad Request")
    def _catalogue(self, query):
        text=query.get("q",[""])[0].casefold(); selected=query.get("filter",["ALL"])[0]
        conflicts={p.drcloud_product_key for p in self.os_repository.all() for other in self.os_repository.by_ean(p.ean) if p.ean and other.drcloud_product_key != p.drcloud_product_key}
        counts=self.service.repo.counts(self.service.session()["id"]); rows=[]
        for p in self.os_repository.all():
            if text and text not in f"{p.name} {p.ean}".casefold(): continue
            if (selected=="WITH_EAN" and not p.ean) or (selected=="WITHOUT_EAN" and p.ean) or (selected=="CONFLICT" and p.drcloud_product_key not in conflicts): continue
            row=asdict(p); row["ean_status"]="CONFLICT" if p.drcloud_product_key in conflicts else "WITH_EAN" if p.ean else "WITHOUT_EAN"
            count=counts.get(p.prestashop_key); row["physical_quantity"]=count["physical_quantity"] if count else None; rows.append(row)
        return rows
    @staticmethod
    def _body(env): return json.loads(env["wsgi.input"].read(int(env.get("CONTENT_LENGTH") or 0)) or b"{}")
    @staticmethod
    def _send(start,body,kind,status="200 OK",headers=None): start(status,[("Content-Type",kind),("Cache-Control","no-store"),*(headers or [])]); return [body]
    def _json(self,start,value,status="200 OK"): return self._send(start,json.dumps(value,ensure_ascii=False).encode(),"application/json; charset=utf-8",status)

def serve(catalogue: Path, validation: Path, database: Path, host="127.0.0.1", port=8080):
    service=InventoryService(catalogue,validation,InventoryRepository(database))
    print(f"DrCloud OS — Inventaire: http://{host}:{port}")
    make_server(host,port,InventoryApp(service,Path("dist/rapport-inventaire-drcloud.json"))).serve_forever()
