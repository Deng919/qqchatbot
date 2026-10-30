from tests.test_web import web_client


def test_desktop_workspace_requires_login(web_client):
    client, _, _ = web_client
    response = client.get('/desktop-workspace', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login'


def test_authenticated_workspace_does_not_change_ordinary_pages(web_client):
    client, _, _ = web_client
    client.post('/login', data={'password': 'password123'})
    workspace = client.get('/desktop-workspace')
    assert workspace.status_code == 200
    assert workspace.headers['cache-control'] == 'no-store'
    assert 'id="desktop-pages"' in workspace.text
    assert 'id="summary-points"' in client.get('/reports').text
    assert 'id="market-list"' in client.get('/secondhand').text


def test_persistent_desktop_pages_in_independent_browser(web_client):
    import os
    from pathlib import Path
    import shutil
    import socket
    import subprocess
    import threading
    import time

    import pytest
    import uvicorn

    node = shutil.which('node')
    if not node or subprocess.run([node, '-e', "require('playwright')"], capture_output=True).returncode:
        pytest.skip('Independent browser verification requires Node and Playwright')
    client, candidate_id, _ = web_client
    # Disable schedulers in this synthetic app; the browser exercises the real authenticated routes.
    app = client.app
    from qq_digest.web.auth import PasswordHasher
    app.state.password_hash = PasswordHasher.hash('navigation-test-password')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.02)
        assert server.started
        root = Path(__file__).resolve().parents[1]
        url = f'http://127.0.0.1:{port}'
        for name, args in [('desktop_navigation_browser.cjs', []), ('desktop_navigation_edges.cjs', [str(candidate_id), '站点'])]:
            result = subprocess.run([node, str(root/'tests'/name), url, *args], cwd=root,
                                    env=os.environ, capture_output=True, text=True, encoding='utf-8', timeout=100)
            assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
