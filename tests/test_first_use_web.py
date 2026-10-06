from datetime import date
from fastapi.testclient import TestClient
import pytest

from tests.test_first_use import setup_env
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher


@pytest.fixture
def setup_client(setup_env):
    path, cfg, archive, source = setup_env
    app = create_app(archive=archive, candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(cfg.resolve_knowledge_paths()), password_hash=PasswordHasher.hash('password123'),
        session_secret='setup-test-secret', config=cfg, config_path=path)
    return TestClient(app), source


def login(client):
    client.post('/login', data={'password': 'password123'})


def post(client, action, **values):
    revision = client.get('/api/setup').json()['revision']
    return client.post('/api/setup/' + action, json={'expected_revision': revision, **values})


def test_auth_strict_inputs_and_configuration_version(setup_client):
    client, source = setup_client
    assert client.get('/api/setup').status_code == 401
    assert client.get('/setup', follow_redirects=False).headers['location'] == '/login'
    login(client)
    assert client.get('/setup').status_code == 200
    assert post(client, 'source', db_dir=str(source), qq_number=True).status_code == 422
    assert post(client, 'source', db_dir=str(source), qq_number=0).status_code == 200
    assert post(client, 'groups', group_ids=[True]).status_code == 422
    assert post(client, 'groups', group_ids=[999]).status_code == 422
    state = post(client, 'groups', group_ids=[100, 200]).json()
    assert client.post('/api/setup/ai-skip', json={'expected_revision': '0'*64}).status_code == 409
    assert post(client, 'import', group_id=100, start_date='2026-10-06', end_date='2026-10-05').status_code == 422
    with client.app.state.operations.claim('backup'):
        assert post(client, 'ai-skip').status_code == 409
        assert client.get('/api/setup').status_code == 200
    assert client.get('/api/scheduler').json()['setup_paused']
    assert not client.get('/api/scheduler').json()['enabled']


def test_complete_flow_without_ai_then_reopen(setup_client):
    client, source = setup_client
    login(client)
    post(client, 'source', db_dir=str(source), qq_number=0)
    post(client, 'groups', group_ids=[100, 200])
    for gid in [100, 200]:
        result = post(client, 'import', group_id=gid, start_date='2026-10-05', end_date='2026-10-05')
        assert result.status_code == 200 and result.json()['imports'][str(gid)]['status'] == 'success'
    assert client.get('/api/setup/preview').json()['total_messages'] == 1
    assert post(client, 'finish').status_code == 422
    state = post(client, 'ai-skip').json()
    result = post(client, 'schedule', auto_collection=False, auto_daily=False, interval_minutes=20,
        hour=21, minute=10, features_revision=state['features_revision'])
    assert result.status_code == 200
    assert post(client, 'finish').json()['completed']
    assert not client.get('/api/scheduler').json()['setup_paused']
    assert client.get('/setup').status_code == 200
    assert client.app.state.config.collection.interval_minutes == 20


def test_ai_test_is_explicit_synthetic_and_key_not_in_responses(setup_client, monkeypatch):
    client, source = setup_client
    login(client)
    captured = []
    class FakeAI:
        def chat(self, messages): captured.extend(messages); return {'ok': True}
        def close(self): pass
    monkeypatch.setattr('qq_digest.ai.factory.build_ai_client', lambda _: FakeAI())
    state = post(client, 'ai', provider='compatible', base_url='https://example.com/v1', model='sample', api_key='test-secret-key')
    assert state.status_code == 200 and state.json()['ai']['key_configured']
    assert not captured and 'test-secret-key' not in state.text
    assert post(client, 'ai-test').json()['ai_ready']
    assert captured == [{'role': 'user', 'content': 'Connection test. Return JSON {"ok": true}.'}]
    assert post(client, 'ai', provider='compatible', base_url='https://user:secret@example.com/v1', model='sample').status_code == 422


def test_read_only_source_is_not_refreshed_on_detection(setup_client, monkeypatch):
    client, source = setup_client
    login(client)
    monkeypatch.setattr('qq_digest.refresh.refresh_database', lambda **_: pytest.fail('must not refresh'))
    assert client.get('/api/setup/detect').status_code == 200
    assert post(client, 'source', db_dir=str(source), qq_number=0).status_code == 200
    assert client.get('/api/setup/groups').status_code == 200


def test_validation_does_not_echo_api_key_and_bridge_model_is_saved(setup_client):
    client, _ = setup_client
    login(client)
    secret = 'private-key-' + 'x'*4100
    result = post(client, 'ai', provider='compatible', base_url='https://example.com', model='sample', api_key=secret)
    assert result.status_code == 422 and 'private-key-' not in result.text
    result = post(client, 'ai', provider='chatgpt_bridge', model='bridge-test')
    assert result.status_code == 200 and client.app.state.config.ai.bridge_model == 'bridge-test'


def test_saved_configuration_updates_desktop_reuse_signature(setup_client):
    from qq_digest.desktop import config_signature
    client, source = setup_client
    login(client)
    svc = client.app.state.first_use
    client.app.state.desktop_config_signature = config_signature(svc.store.path)
    previous = client.get('/desktop-info').json()['config_signature']
    assert post(client, 'source', db_dir=str(source), qq_number=0).status_code == 200
    current = client.get('/desktop-info').json()['config_signature']
    assert current != previous and current == config_signature(svc.store.path)


def test_editing_completed_schedule_reenters_pause(setup_client):
    client, source = setup_client
    login(client)
    post(client, 'source', db_dir=str(source), qq_number=123456)
    post(client, 'groups', group_ids=[100])
    post(client, 'import', group_id=100, start_date='2026-10-05', end_date='2026-10-05')
    state = post(client, 'ai-skip').json()
    state = post(client, 'schedule', auto_collection=False, auto_daily=False, interval_minutes=20,
        hour=21, minute=10, features_revision=state['features_revision']).json()
    post(client, 'finish')
    state = post(client, 'schedule', auto_collection=True, auto_daily=False, interval_minutes=5,
        hour=22, minute=0, features_revision=state['features_revision']).json()
    assert state['active'] and not state['completed']
    assert client.get('/api/scheduler').json()['setup_paused']
    assert not client.get('/api/scheduler').json()['collection']['enabled']
    post(client, 'finish')
    state = post(client, 'groups', group_ids=[100, 200])
    assert state.status_code == 200 and state.json()['active']
    assert state.json()['group_names']['100'] == '研发'


def test_cancelled_http_request_keeps_worker_lock_and_persists_import(setup_client, monkeypatch):
    import asyncio
    import httpx
    from threading import Event
    from qq_digest.collector.ntqq import NTQQCollector
    client, source = setup_client
    login(client)
    post(client, 'source', db_dir=str(source), qq_number=0)
    state = post(client, 'groups', group_ids=[100]).json()
    entered, release = Event(), Event()
    original = NTQQCollector.collect
    def blocking(self, *args):
        entered.set()
        assert release.wait(10)
        return original(self, *args)
    monkeypatch.setattr(NTQQCollector, 'collect', blocking)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://testserver', cookies=client.cookies) as http:
            task = asyncio.create_task(http.post('/api/setup/import', json={'expected_revision':state['revision'], 'group_id':100, 'start_date':'2026-10-05', 'end_date':'2026-10-05'}))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            assert client.app.state.operations.is_active('setup_mutation')
            assert not client.app.state.operations.can_start('backup')
            release.set()
            for _ in range(100):
                if not client.app.state.operations.is_active('setup_mutation'): break
                await asyncio.sleep(.05)
            assert not client.app.state.operations.is_active('setup_mutation')
    try: asyncio.run(scenario())
    finally: release.set()
    assert client.get('/api/setup').json()['imports']['100']['status'] == 'success'
