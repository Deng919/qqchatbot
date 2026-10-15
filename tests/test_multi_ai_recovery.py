from pathlib import Path
import pytest
from tests.test_multi_ai_web import client
from tests.test_first_use import setup_env


def profile(**updates):
    return dict(id='extra', name='Extra', base_url='https://other.example/v1', model='model', api_key='synthetic-new', **updates)


def revision(client):
    return client.get('/api/ai-settings/connections').json()['revision']


def save(client, **updates):
    return client.put('/api/ai-settings/connections', json={**profile(**updates), 'expected_revision': revision(client)})


def test_stale_revision_does_not_replace_key_or_settings(client):
    old = revision(client)
    assert save(client).status_code == 200
    key = Path(client.app.state.config.ai.connections[0].ui_api_key_file)
    response = client.put('/api/ai-settings/connections', json={**profile(), 'api_key': 'synthetic-stale', 'expected_revision': old})
    assert response.status_code == 409
    assert key.read_text().strip() == 'synthetic-new'


def test_busy_save_writes_no_key(client):
    rev = revision(client)
    with client.app.state.operations.claim('backup'):
        response = client.put('/api/ai-settings/connections', json={**profile(), 'expected_revision':rev})
    assert response.status_code == 409
    assert not list((client.app.state.config.data_dir/'config'/'secrets').glob('*'))


def test_yaml_failure_preserves_old_key(client, monkeypatch):
    assert save(client).status_code == 200
    cfg = client.app.state.config
    old = Path(cfg.ai.connections[0].ui_api_key_file)
    rev = revision(client)
    monkeypatch.setattr(client.app.state.first_use.store, 'save', lambda *args: (_ for _ in ()).throw(OSError('synthetic failure')))
    response = client.put('/api/ai-settings/connections', json={**profile(), 'api_key':'synthetic-replacement', 'expected_revision':rev})
    assert response.status_code == 503
    assert cfg.resolve_api_key(cfg.ai.connections[0]) == 'synthetic-new'
    assert list(old.parent.iterdir()) == [old]


def test_cleanup_failure_reported_without_false_failure(client, monkeypatch):
    assert save(client).status_code == 200
    key = Path(client.app.state.config.ai.connections[0].ui_api_key_file)
    original=Path.unlink
    def fail(path,*args,**kwargs):
        if path == key: raise PermissionError('synthetic lock')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail)
    response=client.delete('/api/ai-settings/connections/extra',params={'expected_revision':revision(client)})
    assert response.status_code == 200
    assert response.json()['key_cleanup_pending'] is True
    assert not client.app.state.config.ai.connections


def test_connection_read_and_test_use_saved_revision(client, monkeypatch):
    assert save(client).status_code == 200
    old = revision(client)
    assert save(client).status_code == 200
    called=[]
    monkeypatch.setattr('qq_digest.web.app._test_ai_connection',lambda cfg,ident:called.append(ident))
    response=client.post('/api/ai-settings/connections/test',json={'id':'extra','expected_revision':old})
    assert response.status_code==409 and not called


def test_validation_response_does_not_echo_key(client):
    response=client.put('/api/ai-settings/connections',json={**profile(),'api_key':'synthetic-hidden','unexpected':'oops','expected_revision':revision(client)})
    assert response.status_code==422
    assert 'synthetic-hidden' not in response.text

def test_connection_read_does_not_acquire_blocking_lock_in_event_loop(client):
    import asyncio, threading, httpx
    owner=threading.get_ident()
    original=client.app.state.first_use.store.lock
    class WorkerOnly:
        def __enter__(self):
            assert threading.get_ident()!=owner, 'blocking config lock acquired on ASGI event loop'
            return original.__enter__()
        def __exit__(self,*args):return original.__exit__(*args)
    client.app.state.first_use.store.lock=WorkerOnly()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),base_url='http://testserver',cookies=client.cookies) as http:
            response=await http.get('/api/ai-settings/connections')
            assert response.status_code==200
    asyncio.run(run())

def test_desktop_signature_refresh_never_deletes_committed_key(client,monkeypatch):
    client.app.state.desktop_config_signature='old-signature'
    from qq_digest.desktop import config_signature
    original=config_signature
    monkeypatch.setattr('qq_digest.desktop.config_signature',lambda *_:(_ for _ in ()).throw(PermissionError('synthetic transient read lock')))
    assert save(client).status_code==200
    cfg=client.app.state.config
    assert cfg.resolve_api_key(cfg.ai.connections[0])=='synthetic-new'
    assert client.app.state.desktop_config_signature==original(client.app.state.first_use.store.path)

def test_deleting_profile_keeps_key_shared_by_relative_legacy_reference(client):
    assert save(client).status_code==200
    cfg=client.app.state.config
    key=Path(cfg.ai.connections[0].ui_api_key_file)
    svc=client.app.state.first_use
    svc.store.save(svc.store.revision,{'ai':{'api_key_file':key.relative_to(cfg.data_dir).as_posix()}},svc._state())
    response=client.delete('/api/ai-settings/connections/extra',params={'expected_revision':revision(client)})
    assert response.status_code==200
    assert key.exists()


def test_connection_test_cancel_keeps_physical_worker_claim(client, monkeypatch):
    import asyncio, threading
    from starlette.requests import Request
    from qq_digest.web.ai_connection_routes import TestPayload
    from qq_digest.operations import OperationBusy
    assert save(client).status_code == 200
    ready, release = threading.Event(), threading.Event()
    def fake_test(cfg, ident):
        ready.set()
        assert release.wait(5)
    monkeypatch.setattr('qq_digest.web.app._test_ai_connection', fake_test)
    endpoint = next(r.endpoint for r in client.app.routes if getattr(r, 'path', '') == '/api/ai-settings/connections/test')
    request = Request({'type':'http','app':client.app,'path':'/api/ai-settings/connections/test','headers':[(b'cookie',('qq_digest_session='+client.cookies.get('qq_digest_session')).encode())]})
    payload = TestPayload(id='extra', expected_revision=revision(client))
    async def run():
        task = asyncio.create_task(endpoint(request, payload))
        try:
            for _ in range(100):
                if ready.is_set(): break
                await asyncio.sleep(.01)
            assert ready.is_set()
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            await asyncio.sleep(.02)
            assert client.app.state.operations.is_active('setup_mutation')
            assert not task.done()
            with pytest.raises(OperationBusy):
                with client.app.state.operations.claim('storage_mutation'): pass
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert not client.app.state.operations.is_active('setup_mutation')
    asyncio.run(run())


def test_post_commit_writer_error_does_not_delete_referenced_key(client,monkeypatch):
    svc=client.app.state.first_use
    original=svc.store.save
    def error_after_commit(*args):
        original(*args)
        raise OSError('synthetic post-commit cleanup failure')
    monkeypatch.setattr(svc.store,'save',error_after_commit)
    response=save(client)
    assert response.status_code==503
    cfg=client.app.state.config
    assert cfg.resolve_api_key(cfg.ai.connections[0])=='synthetic-new'
