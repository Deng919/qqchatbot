from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_first_use import setup_env
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher


@pytest.fixture
def client(setup_env):
    path, cfg, archive, _ = setup_env
    app = create_app(archive=archive, candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(cfg.resolve_knowledge_paths()), password_hash=PasswordHasher.hash('password123'),
        session_secret='multi-ai-test-secret', config=cfg, config_path=path)
    value = TestClient(app)
    value.post('/login', data={'password': 'password123'})
    return value


def test_connection_save_and_purpose_selection_are_persistent_without_key_echo(client):
    response = put(client, '/api/ai-settings/connections', {
        'id': 'other', 'name': '其他接口', 'base_url': 'https://other.example/v1',
        'model': 'other-model', 'supports_vision': True, 'api_key': 'synthetic-secret',
    })
    assert response.status_code == 200
    assert 'synthetic-secret' not in response.text
    data = client.get('/api/ai-settings').json()
    assert data['connections'][0]['key_configured'] is True
    assert data['connections'][0]['base_url'] == 'https://other.example/v1'
    assert 'synthetic-secret' not in str(data)
    assert put(client, '/api/ai-settings/purposes', {
        'purpose_connections': {'summary': 'other', 'vision': 'other'}
    }).status_code == 200
    assert client.get('/api/ai-settings').json()['purpose_connections']['summary'] == 'other'
    assert client.app.state.config.ai.connection_for('summary').model == 'other-model'


def test_referenced_connection_cannot_be_deleted_until_unselected(client):
    assert put(client, '/api/ai-settings/connections', {
        'id': 'other', 'name': '其他接口', 'base_url': 'https://other.example/v1',
        'model': 'other-model', 'api_key': 'synthetic-secret',
    }).status_code == 200
    assert put(client, '/api/ai-settings/purposes', {'purpose_connections': {'summary': 'other'}}).status_code == 200
    assert client.delete('/api/ai-settings/connections/other', params={'expected_revision': revision(client)}).status_code == 409
    assert put(client, '/api/ai-settings/purposes', {'purpose_connections': {}}).status_code == 200
    key_path = Path(client.app.state.config.ai.connections[0].ui_api_key_file)
    assert key_path.is_file()
    assert client.delete('/api/ai-settings/connections/other', params={'expected_revision': revision(client)}).status_code == 200
    assert not key_path.exists()
    assert client.get('/api/ai-settings').json().get('connections', []) == []


def test_invalid_connection_does_not_leave_pending_or_final_key(client):
    response = put(client, '/api/ai-settings/connections', {
        'id': 'bad', 'name': '坏地址', 'base_url': 'https://user:pass@example.com/v1',
        'model': 'bad-model', 'api_key': 'synthetic-secret',
    })
    assert response.status_code == 422
    secrets = client.app.state.config.data_dir / 'config' / 'secrets'
    if secrets.exists():
        assert not list(secrets.glob('ai-bad*'))


def test_invalid_purpose_and_unauthenticated_connection_api_rejected(client):
    assert put(client, '/api/ai-settings/purposes', {'purpose_connections': {'summary': 'missing'}}).status_code == 422
    client.cookies.clear()
    assert client.get('/api/ai-settings').status_code == 401
    assert put(client, '/api/ai-settings/connections', {
        'id': 'x', 'name': 'x', 'base_url': 'https://x.example/v1', 'model': 'x'
    }).status_code == 401


def test_connection_test_uses_selected_profile(client, monkeypatch):
    assert put(client, '/api/ai-settings/connections', {
        'id': 'other', 'name': '其他接口', 'base_url': 'https://other.example/v1',
        'model': 'other-model', 'api_key': 'synthetic-secret',
    }).status_code == 200
    called = []
    monkeypatch.setattr('qq_digest.web.app._test_ai_connection', lambda cfg, ident: called.append(ident))
    assert client.post('/api/ai-settings/connections/test', json={'id': 'other', 'expected_revision':revision(client)}).status_code == 200
    assert called == ['other']


def revision(client):
    data=client.get('/api/ai-settings/connections').json()
    return data.get('revision','0'*64)


def put(client,path,payload):
    return client.put(path,json={**payload,'expected_revision':revision(client)})
