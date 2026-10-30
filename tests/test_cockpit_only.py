from pathlib import Path
import pytest
import yaml
from tests.test_first_use import setup_env
from fastapi.testclient import TestClient
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher

@pytest.fixture
def client(setup_env):
    path,cfg,archive,_=setup_env
    app=create_app(archive=archive,candidates=CandidateService(archive),knowledge=KnowledgeWriter(cfg.resolve_knowledge_paths()),password_hash=PasswordHasher.hash('password123'),session_secret='cockpit-test-session',config=cfg,config_path=path)
    value=TestClient(app)
    value.post('/login',data={'password':'password123'})
    return value
from qq_digest.ai.factory import build_ai_client
from qq_digest.ai.client import AIClient
from qq_digest.config import AIConfig

@pytest.mark.parametrize('purpose', ['summary','report_qa','archive_qa','vision','connection_test','secondhand'])
def test_every_purpose_uses_single_cockpit_connection(setup_env,monkeypatch,purpose):
    _,cfg,_,_=setup_env
    cfg.ai=AIConfig(base_url='http://127.0.0.1:62406/v1',model='gpt-6.1-sol',api_key_env='COCKPIT_TEST_KEY',
        provider_priority=['chatgpt_bridge','compatible'],connections=[{'id':'other','name':'Other','base_url':'https://other.example/v1','model':'other','supports_vision':True}],purpose_connections={'summary':'other'})
    monkeypatch.setenv('COCKPIT_TEST_KEY','synthetic-cockpit-key')
    value=build_ai_client(cfg,purpose=purpose)
    try:
        assert type(value) is AIClient
        assert str(value.base_url).rstrip('/')=='http://127.0.0.1:62406/v1'
        assert value.model=='gpt-6.1-sol'
    finally:value.close()

def test_settings_save_preserves_key_and_rejects_stale_revision(client):
    before=client.get('/api/ai-settings').json()
    payload={'expected_revision':before.get('revision','a'*64),'base_url':'http://127.0.0.1:62406/v1','model':'gpt-6.1-sol','json_mode':False,'api_key':'synthetic-secret'}
    result=client.put('/api/ai-settings',json=payload)
    assert result.status_code==200
    assert 'synthetic-secret' not in result.text
    cfg=client.app.state.config
    key=Path(cfg.ai.ui_api_key_file)
    assert key.is_file() and cfg.resolve_api_key()=='synthetic-secret'
    assert client.put('/api/ai-settings',json=payload).status_code==409
    payload.update(expected_revision=result.json()['revision'],model='new-model',api_key='')
    result=client.put('/api/ai-settings',json=payload)
    assert result.status_code==200 and cfg.resolve_api_key()=='synthetic-secret'
    raw=yaml.safe_load(client.app.state.first_use.store.path.read_text('utf-8'))
    assert raw['custom']=={'preserve':42}
    assert not {'provider_priority','connections','purpose_connections'} & set(raw['ai'])

def test_settings_and_setup_have_single_cockpit_form(client):
    for route in ['/ai-settings','setup']:
        page=client.get(route).text
        assert 'Cockpit' in page
        assert 'chatgpt_bridge' not in page and 'DeepSeek' not in page
        assert 'id="multi-ai-form"' not in page and 'id="setup-provider"' not in page
    assert client.put('/api/ai-settings/purposes',json={}).status_code==404

def test_settings_invalid_secret_and_rollback_keep_old_connection(client,monkeypatch):
    cfg=client.app.state.config
    revision=client.get('/api/ai-settings').json()['revision']
    payload=dict(expected_revision=revision,base_url='http://127.0.0.1:62406/v1',model='gpt-6.1-sol',api_key='synthetic-old')
    saved=client.put('/api/ai-settings',json=payload)
    assert saved.status_code==200
    old_path=Path(cfg.ai.ui_api_key_file)
    before=client.app.state.first_use.store.path.read_bytes()
    payload.update(expected_revision=saved.json()['revision'],api_key='synthetic secret')
    invalid=client.put('/api/ai-settings',json=payload)
    assert invalid.status_code==422 and 'synthetic secret' not in invalid.text
    assert cfg.resolve_api_key()=='synthetic-old'
    payload['api_key']='synthetic-new'
    def fail_save(*args):raise OSError('storage failure')
    monkeypatch.setattr(client.app.state.first_use.store,'save',fail_save)
    assert client.put('/api/ai-settings',json=payload).status_code==503
    assert client.app.state.first_use.store.path.read_bytes()==before
    assert cfg.resolve_api_key()=='synthetic-old'
    assert list(old_path.parent.glob('ai-key-*'))==[old_path]


def test_settings_conflicts_and_busy_never_write_key_or_call_ai(client,monkeypatch):
    revision=client.get('/api/ai-settings').json()['revision']
    calls=[]
    monkeypatch.setattr('qq_digest.web.app._test_ai_connection',lambda cfg:calls.append(True))
    assert client.post('/api/ai-settings/test',json={'expected_revision':revision}).status_code==200
    with client.app.state.operations.claim('backup'):
        assert client.post('/api/ai-settings/test',json={'expected_revision':revision}).status_code==409
    assert client.post('/api/ai-settings/test',json={'expected_revision':'0'*64}).status_code==409
    assert calls==[True]
    store=client.app.state.first_use.store
    store.path.write_bytes(store.path.read_bytes()+b'\n')
    payload=dict(expected_revision=revision,base_url='http://127.0.0.1:62406/v1',model='gpt-6.1-sol',api_key='synthetic-new')
    assert client.put('/api/ai-settings',json=payload).status_code==409
    assert not (client.app.state.config.data_dir/'config'/'secrets').exists()


def test_settings_auth_and_validation_hide_secrets(client):
    response=client.put('/api/ai-settings',json={'api_key':'synthetic-private-body'})
    assert response.status_code==422 and 'synthetic-private-body' not in response.text
    client.cookies.clear()
    assert client.get('/api/ai-settings').status_code==401
    assert client.put('/api/ai-settings',json=dict(expected_revision='a'*64,base_url='http://localhost:62406/v1',model='model')).status_code==401
    assert client.post('/api/ai-settings/test',json={'expected_revision':'a'*64}).status_code==401

def test_setup_reports_configured_environment_key(setup_env,monkeypatch):
    from qq_digest.first_use import FirstUseService
    path,cfg,archive,_=setup_env
    monkeypatch.setenv(cfg.ai.api_key_env,'synthetic-existing-key')
    assert FirstUseService(cfg,path,archive).snapshot()['ai']['key_configured'] is True


def test_setup_post_commit_snapshot_failure_keeps_installed_key(setup_env,monkeypatch):
    from qq_digest.first_use import FirstUseService
    path,cfg,archive,_=setup_env
    svc=FirstUseService(cfg,path,archive)
    def fail_snapshot():raise OSError('snapshot failure after atomic config commit')
    monkeypatch.setattr(svc,'snapshot',fail_snapshot)
    with pytest.raises(OSError):svc.set_ai(svc.store.revision,'http://127.0.0.1:62406/v1','gpt-6.1-sol','synthetic-installed-key')
    assert cfg.resolve_api_key()=='synthetic-installed-key'

@pytest.mark.parametrize('legacy_url',['auto','',' ',' auto ','\tauto\n',' http://127.0.0.1:62406/v1 '])
def test_legacy_auto_endpoint_migrates_without_account_discovery(setup_env,legacy_url):
    from qq_digest.config import load_config
    path,_,_,_=setup_env
    raw=yaml.safe_load(path.read_text('utf-8'))
    raw['ai']['base_url']=legacy_url
    path.write_text(yaml.safe_dump(raw),encoding='utf-8')
    cfg=load_config(path,create_dirs=False)
    assert cfg.resolve_base_url()=='http://127.0.0.1:62406/v1'


def test_desktop_does_not_reuse_pre_cockpit_backend(tmp_path,monkeypatch):
    import io,json
    from qq_digest import desktop
    path=tmp_path/'config.yaml';path.write_text('synthetic')
    monkeypatch.setattr(desktop,'service_ready',lambda url:True)
    info=dict(config_signature=desktop.config_signature(path),settings_api_version=1,backend_id='2026-10-30-ui-layout-audit')
    monkeypatch.setattr(desktop.urllib.request,'urlopen',lambda *args,**kwargs:io.BytesIO(json.dumps(info).encode()))
    assert desktop.matching_service('http://127.0.0.1:8765/',path) is False
    info['backend_id']=desktop.DESKTOP_BACKEND_ID
    assert desktop.matching_service('http://127.0.0.1:8765/',path) is True

def test_connection_test_cancel_keeps_physical_worker_claim(client,monkeypatch):
    import asyncio,threading
    from starlette.requests import Request
    from qq_digest.web.ai_connection_routes import VersionPayload
    from qq_digest.operations import OperationBusy
    ready,release=threading.Event(),threading.Event()
    def fake_test(cfg):
        ready.set()
        assert release.wait(5)
    monkeypatch.setattr('qq_digest.web.app._test_ai_connection',fake_test)
    endpoint=next(r.endpoint for r in client.app.routes if getattr(r,'path','')=='/api/ai-settings/test')
    request=Request({'type':'http','app':client.app,'path':'/api/ai-settings/test','headers':[(b'cookie',('qq_digest_session='+client.cookies.get('qq_digest_session')).encode())]})
    payload=VersionPayload(expected_revision=client.get('/api/ai-settings').json()['revision'])
    async def run():
        task=asyncio.create_task(endpoint(request,payload))
        try:
            for _ in range(100):
                if ready.is_set():break
                await asyncio.sleep(.01)
            assert ready.is_set()
            task.cancel();await asyncio.sleep(.02)
            task.cancel();await asyncio.sleep(.02)
            assert client.app.state.operations.is_active('setup_mutation') and not task.done()
            with pytest.raises(OperationBusy):
                with client.app.state.operations.claim('storage_mutation'):pass
        finally:release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert not client.app.state.operations.is_active('setup_mutation')
    asyncio.run(run())


def test_settings_post_commit_error_keeps_installed_key(client,monkeypatch):
    store=client.app.state.first_use.store
    original=store.save
    def error_after_commit(*args):
        original(*args)
        raise OSError('synthetic post-commit failure')
    monkeypatch.setattr(store,'save',error_after_commit)
    revision=client.get('/api/ai-settings').json()['revision']
    result=client.put('/api/ai-settings',json=dict(expected_revision=revision,base_url='http://127.0.0.1:62406/v1',model='gpt-6.1-sol',api_key='synthetic-installed-key'))
    assert result.status_code==503
    assert client.app.state.config.resolve_api_key()=='synthetic-installed-key'


@pytest.mark.parametrize('url',['',' ','\t\n','auto',' auto '])
@pytest.mark.parametrize('route',['/api/ai-settings','/api/setup/ai'])
def test_user_submitted_blank_url_is_rejected_without_mutation(client,url,route):
    store=client.app.state.first_use.store
    before=store.path.read_bytes()
    payload=dict(expected_revision=store.revision,base_url=url,model='gpt-6.1-sol',api_key='synthetic-new-key')
    result=client.request('PUT' if route=='/api/ai-settings' else 'POST',route,json=payload)
    assert result.status_code==422
    assert store.path.read_bytes()==before
    assert not (client.app.state.config.data_dir/'config'/'secrets').exists()
