import io, json, os, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor
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

def test_health_can_query_sqlite_from_a_waitress_worker_thread(configured):
    app, _ = configured
    with ThreadPoolExecutor(max_workers=1) as executor:
        status, _, body = executor.submit(request, app, "/health").result()
    value = json.loads(body)
    assert status == "200 OK"
    assert value["status"] == "ok"
    assert value["database"] == "ok"

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


def test_deployment_metadata_mount_is_minimal_and_read_only():
    root = Path(__file__).parents[1]
    compose = (root / "deploy/ovh/docker-compose.yml").read_text()
    dockerfile = (root / "Dockerfile").read_text()
    assert "${DRCLOUD_DEPLOYMENT_STATE_DIR:?set by deploy.sh}:/run/drcloud-deployment:ro" in compose
    assert "DRCLOUD_DEPLOYMENT_MARKER: /run/drcloud-deployment/last-successful-commit" in compose
    assert "/opt/drcloud-os" not in compose
    assert "/.git" not in compose and "docker.sock" not in compose
    assert "USER drcloud" in dockerfile


def test_success_marker_is_published_only_after_checks_and_rollback_validation():
    script = (Path(__file__).parents[1] / "deploy/ovh/update.sh").read_text()
    success_check = script.index('EXPECTED_COMMIT="$target"')
    success_publish = script.index('record_successful_commit "$target"')
    rollback_check = script.index('EXPECTED_COMMIT="$previous"')
    rollback_publish = script.index('record_successful_commit "$previous"')
    assert success_check < success_publish < rollback_check < rollback_publish
    publisher = (Path(__file__).parents[1] / "deploy/ovh/deployment-state.sh").read_text()
    assert 'mv -f -- "$tmp"' in publisher  # Atomic rename, not in-place mutation.
    assert 'chmod 0444 "$tmp"' in publisher


def test_first_install_creates_state_directory_without_claiming_success():
    root = Path(__file__).parents[1]
    deploy = (root / "deploy/ovh/deploy.sh").read_text()
    assert '[[ -d "$DRCLOUD_DEPLOYMENT_STATE_DIR" ]]' in deploy
    assert "record_successful_commit" not in deploy


def test_deployment_writer_can_atomically_replace_read_only_marker(tmp_path):
    root = Path(__file__).parents[1]
    publisher = root / "deploy/ovh/deployment-state.sh"
    compose = (root / "deploy/ovh/docker-compose.yml").read_text()
    state_dir = tmp_path / ".deployment-state"
    state_dir.mkdir(mode=0o755)
    state_dir.chmod(0o755)
    user = subprocess.run(["id", "-un"], check=True, text=True, capture_output=True).stdout.strip()
    group = subprocess.run(["id", "-gn"], check=True, text=True, capture_output=True).stdout.strip()
    env = {**os.environ, "DRCLOUD_DEPLOY_USER": user, "DRCLOUD_DEPLOY_GROUP": group}

    for commit in ("a" * 40, "b" * 40):
        result = subprocess.run(
            [publisher, state_dir, commit], env=env, check=False, text=True, capture_output=True
        )
        assert result.returncode == 0, result.stderr
        marker = state_dir / "last-successful-commit"
        assert marker.read_text() == f"{commit}\n"
        assert marker.stat().st_mode & 0o777 == 0o444

    assert state_dir.stat().st_mode & 0o777 == 0o755
    assert ":/run/drcloud-deployment:ro" in compose
    scripts = "".join(path.read_text() for path in (root / "deploy/ovh").glob("*.sh"))
    assert "chmod 777" not in scripts and "chmod 0777" not in scripts


def test_state_provisioning_has_explicit_minimal_ownership_contract():
    root = Path(__file__).parents[1]
    prepare = (root / "deploy/ovh/prepare-deployment-state.sh").read_text()
    publisher = (root / "deploy/ovh/deployment-state.sh").read_text()
    assert "drcloud-deploy" in prepare and 'chmod 0755 "$state_dir"' in prepare
    assert 'chown "$deploy_user:$deploy_group" "$state_dir"' in prepare
    assert 'expected_user="${DRCLOUD_DEPLOY_USER:-drcloud-deploy}"' in publisher
    assert "777" not in prepare


def test_deployment_state_is_canonical_and_independent_from_working_directory(tmp_path):
    root = Path(__file__).parents[1]
    update = (root / "deploy/ovh/update.sh").read_text()
    deploy = (root / "deploy/ovh/deploy.sh").read_text()
    compose = (root / "deploy/ovh/docker-compose.yml").read_text()

    assert 'export DRCLOUD_DEPLOYMENT_STATE_DIR="$state_dir"' in update
    assert update.count('DRCLOUD_DEPLOYMENT_STATE_DIR="$state_dir" DRCLOUD_BUILD_COMMIT=') == 2
    assert '${DRCLOUD_DEPLOYMENT_STATE_DIR:-$PWD/' not in deploy
    assert "DRCLOUD_DEPLOYMENT_STATE_DIR:-./" not in compose

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    (fake_bin / "docker").write_text(
        '#!/bin/sh\nprintf "%s|%s\\n" "$DRCLOUD_DEPLOYMENT_STATE_DIR" "$*" >> "$DOCKER_LOG"\n'
    )
    (fake_bin / "curl").write_text(
        '#!/bin/sh\nprintf \'{"status":"ok","commit":"%s"}\\n\' "$DRCLOUD_BUILD_COMMIT"\n'
    )
    for executable in (fake_bin / "docker", fake_bin / "curl"):
        executable.chmod(0o755)

    env_file = root / "deploy/ovh/drcloud.env"
    original_env = env_file.read_bytes() if env_file.exists() else None
    env_file.write_text(
        "DRCLOUD_SAFE_MODE=true\nBARCODE_SYNC_MODE=dry-run\n"
        "DRCLOUD_SECRET_KEY=test\nDRCLOUD_ADMIN_USERNAME=test\nDRCLOUD_ADMIN_PASSWORD=test\n"
    )
    accidental = tmp_path / "elsewhere" / ".deployment-state"
    cwd = accidental.parent
    cwd.mkdir()
    expected_state = root / "deploy/ovh/.deployment-state"
    expected_state.mkdir(mode=0o755, exist_ok=True)
    try:
        result = subprocess.run(
            [root / "deploy/ovh/deploy.sh"], cwd=cwd, check=False, text=True,
            capture_output=True,
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "DOCKER_LOG": str(docker_log), "DRCLOUD_BUILD_COMMIT": "a" * 40},
        )
    finally:
        if original_env is None:
            env_file.unlink()
        else:
            env_file.write_bytes(original_env)
        expected_state.rmdir()

    assert result.returncode == 0, result.stderr
    expected = str(expected_state.resolve())
    invocations = docker_log.read_text().splitlines()
    assert invocations and all(line.startswith(f"{expected}|") for line in invocations)
    assert not accidental.exists()


def test_compose_validation_requires_an_explicit_absolute_state_directory():
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is not available")

    root = Path(__file__).parents[1]
    compose_file = root / "deploy/ovh/docker-compose.yml"
    env_file = root / "deploy/ovh/drcloud.env"
    original_env = env_file.read_bytes() if env_file.exists() else None
    env_file.write_text("")
    environment = os.environ.copy()
    environment.pop("DRCLOUD_DEPLOYMENT_STATE_DIR", None)
    command = ["docker", "compose", "-f", str(compose_file), "config", "--quiet"]
    try:
        missing = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
        assert missing.returncode != 0
        assert "DRCLOUD_DEPLOYMENT_STATE_DIR" in missing.stderr

        state_dir = (root / "deploy/ovh/.deployment-state").resolve()
        configured = subprocess.run(
            command,
            cwd=root,
            env={**environment, "DRCLOUD_DEPLOYMENT_STATE_DIR": str(state_dir)},
            capture_output=True,
            text=True,
        )
        assert configured.returncode == 0, configured.stderr
    finally:
        if original_env is None:
            env_file.unlink()
        else:
            env_file.write_bytes(original_env)


def test_ci_provides_absolute_state_directory_when_validating_compose():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/drcloud-os-ci.yml").read_text()
    assert 'DRCLOUD_DEPLOYMENT_STATE_DIR="${{ github.workspace }}/deploy/ovh/.deployment-state"' in workflow
    assert "docker compose -f deploy/ovh/docker-compose.yml config --quiet" in workflow
