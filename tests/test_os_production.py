import io, json, os
from pathlib import Path
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults
import pytest
from dr_cloud_sync.connectors import SafeModeViolation, assert_external_write_allowed
from dr_cloud_sync.inventory import InventoryRepository, InventoryService
from dr_cloud_sync.inventory_web import InventoryApp
from dr_cloud_sync.os_admin import backup, init_catalog
from dr_cloud_sync.os_config import OSSettings


def mapping(tmp_path):
    rows=[{"prestashop_key":f"p:{i}","drcloud_product_key":f"drc:p:{i}","product_id":i,"combination_id":0,"name":f"Produit {i}","ean":"","shopcaisse_item_id":f"sc-{i}"} for i in range(478)]
    source=tmp_path/'mapping.json'; source.write_text(json.dumps({'mappings':rows})); report=tmp_path/'report.json';report.write_text('{"ready_for_inventory":true}')
    return source,report

@pytest.fixture
def configured(tmp_path):
    source,report=mapping(tmp_path); db=tmp_path/'data'/'drcloud.db'; init_catalog(source,report,db)
    settings=OSSettings('production','x'*40,'admin','very-secret-password',db.parent,'0.0.0.0',8080,True,False)
    app=InventoryApp(InventoryService(db.parent/'catalogue.json',db.parent/'catalogue-report.json',InventoryRepository(db)),settings=settings)
    return app,settings

def request(app,path,method='GET',body=None,cookie=None,headers=None):
    env={};setup_testing_defaults(env);env['PATH_INFO']=path;env['REQUEST_METHOD']=method
    raw=(body.encode() if isinstance(body,str) else json.dumps(body).encode() if body is not None else b'');env['wsgi.input']=io.BytesIO(raw);env['CONTENT_LENGTH']=str(len(raw))
    if cookie:env['HTTP_COOKIE']=cookie
    for k,v in (headers or {}).items():env['HTTP_'+k.upper().replace('-','_')]=v
    result=[];payload=b''.join(app(env,lambda s,h:result.append((s,h))))
    return result[0][0],dict(result[0][1]),payload

def login(app,password='very-secret-password'):
    status,headers,_=request(app,'/login','POST',urlencode({'username':'admin','password':password}));return status,headers.get('Set-Cookie','')

def test_auth_login_logout_csrf_and_secure_cookie(configured):
    app,_=configured
    assert request(app,'/')[0]=='303 See Other'
    assert login(app,'wrong')[0]=='401 Unauthorized'
    status,cookie=login(app);assert status=='303 See Other' and 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=Lax' in cookie
    assert request(app,'/roadmap',cookie=cookie)[0]=='200 OK';assert request(app,'/catalogue',cookie=cookie)[0]=='200 OK';assert request(app,'/inventaire',cookie=cookie)[0]=='200 OK'
    assert request(app,'/logout','POST',cookie=cookie)[0]=='403 Forbidden'
    session=app._session({'HTTP_COOKIE':cookie}); assert request(app,'/logout','POST',urlencode({'csrf_token':session['csrf']}),cookie)[0]=='303 See Other'

def test_health_manifest_dashboard_and_no_browser_secrets(configured):
    app,_=configured; status,_,body=request(app,'/health'); value=json.loads(body);assert status=='200 OK' and value['database']=='ok' and set(value)=={'status','application','version','commit','build_date','database'}
    assert request(app,'/manifest.webmanifest')[0]=='200 OK'
    _,cookie=login(app); html=request(app,'/',cookie=cookie)[2].decode(); assert 'DrCloud OS' in html and 'Mode sécurisé' in html
    assets=''.join(p.read_text() for p in (Path(__file__).parents[1]/'src/dr_cloud_sync/static').iterdir())
    assert 'SHOPCAISSE_API_KEY' not in assets and 'PRESTASHOP_API_KEY' not in assets and 'DRCLOUD_ADMIN_PASSWORD' not in assets and 'very-secret-password' not in html

def test_health_exposes_non_secret_build_identity(configured, monkeypatch):
    monkeypatch.setenv("DRCLOUD_BUILD_COMMIT", "a" * 40)
    monkeypatch.setenv("DRCLOUD_BUILD_DATE", "2026-07-29T00:00:00Z")
    app, _ = configured
    status, _, body = request(app, "/health")
    value = json.loads(body)
    assert status == "200 OK"
    assert value["commit"] == "a" * 40
    assert value["build_date"] == "2026-07-29T00:00:00Z"

def test_data_dir_catalog_idempotence_and_secret_free_backup(tmp_path):
    source,report=mapping(tmp_path);db=tmp_path/'volume'/'drcloud.db';assert init_catalog(source,report,db)==478;assert init_catalog(source,report,db)==478
    target=backup(db,tmp_path/'backups',environment='production',safe_mode=True); metadata=(target/'metadata.json').read_text();assert (target/'drcloud.db').exists() and 'password' not in metadata.lower() and 'api_key' not in metadata.lower()

def test_safe_defaults_block_mutations_but_allow_get(monkeypatch):
    monkeypatch.delenv('DRCLOUD_SAFE_MODE',raising=False);monkeypatch.delenv('BARCODE_SYNC_MODE',raising=False)
    assert OSSettings.from_env(require_secrets=False).safe_mode is True;assert os.environ.get('BARCODE_SYNC_MODE','dry-run')=='dry-run'
    for system in ('PrestaShop','ShopCaisse'):
        for method in ('POST','PUT','PATCH','DELETE'):
            with pytest.raises(SafeModeViolation):assert_external_write_allowed(system,method)
        assert_external_write_allowed(system,'GET')
