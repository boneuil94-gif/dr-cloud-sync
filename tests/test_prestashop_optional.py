import json

import pytest

from dr_cloud_sync.inventory_web import create_app
from dr_cloud_sync.os_admin import init_catalog
from test_os_production import login, mapping, request


def boot_environment(monkeypatch, tmp_path):
    source,report=mapping(tmp_path); data=tmp_path/"data"
    init_catalog(source,report,data/"drcloud.db")
    values={"DRCLOUD_ENV":"production","DRCLOUD_SECRET_KEY":"x"*40,
            "DRCLOUD_ADMIN_USERNAME":"admin","DRCLOUD_ADMIN_PASSWORD":"very-secret-password",
            "DRCLOUD_DATA_DIR":str(data),"INVENTORY_CATALOGUE":str(data/"catalogue.json"),
            "INVENTORY_MAPPING_REPORT":str(data/"catalogue-report.json")}
    for name,value in values.items(): monkeypatch.setenv(name,value)
    return data


@pytest.mark.parametrize("url,key,state",[
    (None,None,"NOT_CONFIGURED"),("",None,"NOT_CONFIGURED"),
    ("not-an-absolute-url","read-only-secret","INVALID_CONFIGURATION"),
    ("https://shop.example/api",None,"NOT_CONFIGURED"),
    ("https://shop.example/api","read-only-secret","CONFIGURED"),
])
def test_create_app_never_requires_prestashop(monkeypatch,tmp_path,url,key,state):
    boot_environment(monkeypatch,tmp_path)
    if url is None: monkeypatch.delenv("PRESTASHOP_API_URL",raising=False)
    else: monkeypatch.setenv("PRESTASHOP_API_URL",url)
    if key is None: monkeypatch.delenv("PRESTASHOP_API_KEY",raising=False)
    else: monkeypatch.setenv("PRESTASHOP_API_KEY",key)
    constructed=[]
    monkeypatch.setattr("dr_cloud_sync.media_import.PrestaShopClient",
                        lambda *a,**k: constructed.append(a))
    app=create_app()
    assert constructed==[]
    status,_,body=request(app,"/health")
    assert status=="200 OK" and json.loads(body)["status"]=="ok"
    assert app.media_import.status()["state"]==state
    assert len(app.os_repository.all())==478


def test_regression_invalid_production_configuration_is_functional_error(monkeypatch,tmp_path):
    boot_environment(monkeypatch,tmp_path)
    monkeypatch.setenv("PRESTASHOP_API_URL","CHANGE_ME")
    monkeypatch.setenv("PRESTASHOP_API_KEY","super-secret-must-not-leak")
    app=create_app(); _,cookie=login(app); csrf=app._session({"HTTP_COOKIE":cookie})["csrf"]
    status,_,body=request(app,"/api/admin/product-media-import/preview","POST",{},cookie,{"X-CSRF-Token":csrf})
    payload=json.loads(body)
    assert status=="503 Service Unavailable"
    assert payload["integration"]["state"]=="INVALID_CONFIGURATION"
    assert "configuration invalide" in payload["error"].lower()
    assert "super-secret-must-not-leak" not in body.decode()
    assert json.loads(request(app,"/health")[2])["status"]=="ok"


@pytest.mark.parametrize("failure",[OSError("dns unavailable"),TimeoutError("timeout")])
def test_network_failure_only_degrades_optional_integration(monkeypatch,tmp_path,failure):
    boot_environment(monkeypatch,tmp_path)
    monkeypatch.setenv("PRESTASHOP_API_URL","https://shop.example/api")
    monkeypatch.setenv("PRESTASHOP_API_KEY","secret")
    app=create_app()
    class BrokenService:
        def preview(self):
            from dr_cloud_sync.prestashop import PrestaShopError
            raise PrestaShopError("PrestaShop indisponible sur products") from failure
    monkeypatch.setattr(app.media_import,"service",lambda:BrokenService())
    _,cookie=login(app); csrf=app._session({"HTTP_COOKIE":cookie})["csrf"]
    status,_,body=request(app,"/api/admin/product-media-import/preview","POST",{},cookie,{"X-CSRF-Token":csrf})
    assert status=="503 Service Unavailable"
    assert json.loads(body)["integration"]["state"]=="UNAVAILABLE"
    assert json.loads(request(app,"/health")[2])["status"]=="ok"
