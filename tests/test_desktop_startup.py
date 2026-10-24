from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qq_digest.desktop import available_port, make_server, service_ready
from qq_digest.web.auth import SessionCookie


SECRET = 'synthetic-desktop-startup-secret-' * 2


@pytest.fixture
def desktop_app(tmp_path):
    config = tmp_path / 'config' / 'config.yaml'
    config.parent.mkdir()
    (config.parent / 'session.txt').write_text(SECRET, 'utf-8')
    config.write_text('security:\n  web_password_hash: synthetic\n  session_secret_file: config/session.txt\n'
                      'ai:\n  base_url: https://invalid.example\n  model: test\n  api_key_env: UNUSED_QA_KEY\n', 'utf-8')
    app = make_server(config, bridge=SimpleNamespace()).config.app
    yield app
    app.state.close_topic_tracking()
    app.state.archive.close()


def proof():
    return SessionCookie('desktop-launch:' + SECRET, 120).issue()


def client(app, host='127.0.0.1'):
    return TestClient(app, base_url='http://127.0.0.1:8765', client=(host, 50000))


def test_empty_port_probe_does_not_wait_for_http_timeout(monkeypatch):
    port = available_port()
    monkeypatch.setattr('qq_digest.desktop.urllib.request.urlopen',
                        lambda *_a, **_k: pytest.fail('an unbound local port needs no HTTP request'))
    assert service_ready(f'http://127.0.0.1:{port}/') is False


def test_desktop_launch_exchanges_proof_for_http_only_session(desktop_app):
    http = client(desktop_app)
    assert http.get('/api/groups').status_code == 401
    response = http.post('/api/desktop-session', json={'token': proof()})
    assert response.status_code == 200
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'no-store' in response.headers['cache-control']
    assert http.get('/api/groups').status_code == 200


def test_desktop_proof_cannot_be_replayed(desktop_app):
    token = proof()
    first = client(desktop_app).post('/api/desktop-session', json={'token': token})
    assert first.status_code == 200
    other = client(desktop_app)
    replay = other.post('/api/desktop-session', json={'token': token})
    assert replay.status_code == 401
    assert other.get('/api/groups').status_code == 401


def test_browser_login_is_still_required(desktop_app):
    http = client(desktop_app)
    page = http.get('/desktop-start')
    assert page.status_code == 200
    assert not page.headers.get('set-cookie')
    assert 'history.replaceState' in page.text
    assert http.post('/api/desktop-session', json={'token': 'invalid'}).status_code == 401
    assert http.get('/reports', follow_redirects=False).status_code == 303


def test_desktop_proof_expires(desktop_app, monkeypatch):
    import time
    token = proof()
    later = time.time() + 121
    monkeypatch.setattr('qq_digest.web.auth.time.time', lambda: later)
    response = client(desktop_app).post('/api/desktop-session', json={'token': token})
    assert response.status_code == 401


def test_remote_request_cannot_exchange_a_desktop_proof(desktop_app):
    token = proof()
    remote = client(desktop_app, '203.0.113.7').post('/api/desktop-session', json={'token': token})
    local = client(desktop_app).post('/api/desktop-session', json={'token': token})
    assert remote.status_code == 403
    assert local.status_code == 200


def test_foreign_origin_cannot_consume_a_desktop_proof(desktop_app):
    token = proof()
    denied = client(desktop_app).post('/api/desktop-session', json={'token': token}, headers={'Origin': 'https://foreign.example'})
    accepted = client(desktop_app).post('/api/desktop-session', json={'token': token})
    assert denied.status_code == 403
    assert accepted.status_code == 200


def test_desktop_proof_consumption_is_atomic():
    from concurrent.futures import ThreadPoolExecutor
    from qq_digest.web.desktop_session import DesktopLaunchTokens
    proofs = DesktopLaunchTokens(SECRET)
    token = proofs.issue()
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(lambda _: proofs.consume(token), range(8)))
    assert sum(accepted) == 1


def test_web_session_cookie_is_not_a_desktop_proof(desktop_app):
    response = client(desktop_app).post('/api/desktop-session', json={'token': SessionCookie(SECRET, 3600).issue()})
    assert response.status_code == 401


def test_native_start_url_keeps_the_proof_out_of_query_parameters():
    from urllib.parse import urlsplit
    from qq_digest.web.desktop_session import DesktopLaunchTokens, desktop_start_url
    address = urlsplit(desktop_start_url('http://127.0.0.1:8765/', SECRET))
    assert address.path == '/desktop-start' and not address.query
    accepted = DesktopLaunchTokens(SECRET).consume(address.fragment)
    assert accepted is True


def test_regular_web_service_has_no_desktop_login(tmp_path):
    config = tmp_path / 'config.yaml'
    (tmp_path / 'session.txt').write_text(SECRET, 'utf-8')
    config.write_text('security:\n  web_password_hash: synthetic\n  session_secret_file: session.txt\n'
                      'ai:\n  base_url: https://invalid.example\n  model: test\n  api_key_env: UNUSED_QA_KEY\n', 'utf-8')
    app = make_server(config).config.app
    try:
        response = client(app).post('/api/desktop-session', json={'token': proof()})
        assert response.status_code == 404
    finally:
        app.state.close_topic_tracking()
        app.state.archive.close()
