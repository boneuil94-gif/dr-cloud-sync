import io, json, sqlite3
from pathlib import Path
import pytest
from dr_cloud_sync.domain import SupplierStatus
from dr_cloud_sync.purchasing import SQLiteSupplierRepository, SupplierService, DuplicateSupplierIdentity
from dr_cloud_sync.repositories import MemoryAuditRepository
from dr_cloud_sync.inventory import InventoryRepository, InventoryService
from dr_cloud_sync.inventory_web import InventoryApp

@pytest.fixture
def service(tmp_path):
    repo=SQLiteSupplierRepository(tmp_path/'os.db'); audit=MemoryAuditRepository()
    return SupplierService(repo,audit)

def test_supplier_domain_crud_identity_validation_and_audit(service):
    supplier,dupes=service.create({'name':'  Acme  '})
    assert supplier.supplier_id.startswith('sup:') and supplier.status is SupplierStatus.ACTIVE
    assert not dupes and supplier.email=='' and len(service.audit.activities())==1
    updated,_=service.update(supplier.supplier_id,{'name':'Acme France','email':'hello@acme.fr'})
    assert updated.supplier_id==supplier.supplier_id and updated.updated_at >= supplier.updated_at
    with pytest.raises(ValueError,match='immutable'): service.update(supplier.supplier_id,{'supplier_id':'sup:other'})
    with pytest.raises(ValueError,match='name'): service.create({'name':' '})
    with pytest.raises(ValueError,match='email'): service.create({'name':'Bad','email':'bad'})

def test_lifecycle_duplicates_and_search(service):
    first,_=service.create({'name':'ACME','contact_name':'Alice','phone':'0102'})
    second,dupes=service.create({'name':' acme '})
    assert [x.supplier_id for x in dupes]==[first.supplier_id]
    assert service.list('alice')==[first] and service.list('0102')==[first]
    service.transition(first.supplier_id,'INACTIVE'); service.transition(first.supplier_id,'ARCHIVED')
    with pytest.raises(ValueError): service.transition(first.supplier_id,'ACTIVE')
    service.transition(first.supplier_id,'INACTIVE'); service.transition(first.supplier_id,'ACTIVE')
    assert service.get(first.supplier_id).status is SupplierStatus.ACTIVE

def test_repository_persistence_migration_and_unique_identity(tmp_path):
    path=tmp_path/'existing.db'; sqlite3.connect(path).execute('CREATE TABLE legacy(value TEXT)').connection.close()
    one=SQLiteSupplierRepository(path); s,_=SupplierService(one,MemoryAuditRepository()).create({'name':'Persist'})
    with pytest.raises(DuplicateSupplierIdentity): one.create(s)
    one.db.close(); two=SQLiteSupplierRepository(path)
    assert two.get(s.supplier_id).name=='Persist'; assert two.list()==two.search('persist')
    assert sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE name='legacy'").fetchone()

def request(app,path,method='GET',body=None,csrf='test'):
    raw=json.dumps(body or {}).encode(); status=[]
    env={'PATH_INFO':path.split('?')[0],'QUERY_STRING':path.partition('?')[2],'REQUEST_METHOD':method,'wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'HTTP_X_CSRF_TOKEN':csrf}
    payload=b''.join(app(env,lambda s,h:status.append(s)))
    return status[0],json.loads(payload) if payload else None

@pytest.fixture
def app(tmp_path):
    catalogue=tmp_path/'catalogue.json'; catalogue.write_text(json.dumps([{"prestashop_key":"1","shopcaisse_item_id":"1","product_id":1,"combination_id":None,"name":"Test"}]))
    validation=tmp_path/'validation.json'; validation.write_text('{"ready_for_inventory":true}')
    return InventoryApp(InventoryService(catalogue,validation,InventoryRepository(tmp_path/'app.db')))

def test_supplier_api_and_ui(app):
    assert b'Achats' in b''.join(app({'PATH_INFO':'/achats','QUERY_STRING':'','REQUEST_METHOD':'GET','wsgi.input':io.BytesIO()},lambda s,h:None))
    assert request(app,'/api/suppliers')[1]=={'suppliers':[]}
    status,created=request(app,'/api/suppliers','POST',{'name':'API','email':'api@example.fr'}); assert status=='201 Created'
    sid=created['supplier']['supplier_id']; assert request(app,f'/api/suppliers/{sid}')[1]['supplier']['name']=='API'
    assert request(app,f'/api/suppliers/{sid}','PATCH',{'name':'API 2'})[1]['supplier']['supplier_id']==sid
    assert request(app,f'/api/suppliers/{sid}/status','POST',{'status':'ARCHIVED'})[1]['status']=='ARCHIVED'
    assert request(app,'/api/suppliers/missing')[0]=='404 Not Found'
    assert request(app,'/api/suppliers','POST',{'name':''})[0]=='400 Bad Request'

def test_authenticated_app_rejects_auth_and_csrf(app):
    class Settings: secret_key='x'; environment='test'; safe_mode=True
    app.settings=Settings()
    assert request(app,'/api/suppliers')[0]=='303 See Other'
