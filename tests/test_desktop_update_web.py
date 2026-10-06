from types import SimpleNamespace

from tests.test_web import web_client


def test_update_api_requires_login_and_desktop(tmp_path):
    client, _, _ = web_client.__wrapped__(tmp_path)
    assert client.get('/api/desktop-updates').status_code == 401
    client.post('/login', data={'password': 'password123'})
    assert client.get('/api/desktop-updates').status_code == 503
    page = client.get('/desktop-updates')
    assert page.status_code == 200 and '版本更新' in page.text
    settings = client.get('/settings')
    assert 'href="/desktop-updates"' in settings.text


def test_update_payload_validation_and_bridge_calls(tmp_path):
    client, _, _ = web_client.__wrapped__(tmp_path)
    client.post('/login', data={'password': 'password123'})
    calls = []
    client.app.state.desktop_bridge = SimpleNamespace(
        get_updates=lambda: {'current': 'old', 'versions': []},
        switch_version=lambda version, digest: calls.append((version, digest)) or {'status': 'prepared'},
        clean_version=lambda version, digest: {'removed': True},
    )
    assert client.get('/api/desktop-updates').json()['current'] == 'old'
    assert client.post('/api/desktop-updates/switch', json={'version': '../bad', 'digest': 'a'*64}).status_code == 422
    assert client.post('/api/desktop-updates/switch', json={'version': 'new', 'digest': 'a'*64}).status_code == 200
    assert calls == [('new', 'a'*64)]
    assert client.post('/api/desktop-updates/clean', json={'version': 'new', 'digest': 'a'*64, 'path': 'D:/'}).status_code == 422
