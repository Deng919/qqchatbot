from tests.test_first_use_web import setup_client, setup_env


def test_setup_pause_has_visible_recovery_outside_wizard(setup_client):
    client, *_ = setup_client
    client.post('/login', data={'password': 'password123'})
    assert 'id="setup-pause-notice"' not in client.get('/reports').text
    state = client.get('/api/setup').json()
    response = client.post('/api/setup/ai-skip', json={'expected_revision': state['revision']})
    assert response.status_code == 200
    page = client.get('/reports').text
    assert 'id="setup-pause-notice"' in page
    assert '首次设置尚未退出' in page
    assert '返回设置' in page
    assert 'id="setup-pause-notice"' not in client.get('/setup').text
    response = client.post('/api/setup/exit', json={'expected_revision': response.json()['revision']})
    assert response.status_code == 200
    assert 'id="setup-pause-notice"' not in client.get('/reports').text
