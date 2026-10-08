import re

from tests.test_web import web_client


def tools(page):
    return re.search(r'<nav[^>]+id="workspace-tools-area"[^>]*>(.*?)</nav>', page, re.S).group(1)


def test_legacy_pages_redirect_after_login_without_restoring_old_ui(web_client):
    client, _, _ = web_client
    for path in ('/catchup', '/ai-settings'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers['location'] == '/login'
    client.post('/login', data={'password': 'password123'})
    for path, target in (('/catchup', '/reports?view=points'), ('/ai-settings', '/settings?section=ai')):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers['location'] == target
    state = client.get('/api/features').json()
    client.put('/api/features', json={'values': {'catchup': False}, 'expected_revision': state['revision']})
    assert client.get('/catchup', follow_redirects=False).headers['location'] == '/reports?view=points'
    assert client.get('/api/catchup').status_code == 403
    assert client.get('/reports').status_code == 200


def test_review_and_failures_have_parent_entries_and_honor_switches(web_client):
    client, _, _ = web_client
    client.post('/login', data={'password': 'password123'})
    knowledge = client.get('/knowledge').text
    review = client.get('/candidates').text
    for page in (knowledge, review, client.get('/overview').text):
        header = tools(page)
        assert 'href="/candidates"' not in header and 'href="/failures"' not in header
        assert 'href="/reports?view=points"' in header
    assert 'href="/candidates"' in knowledge
    assert re.search(r'href="/knowledge"[^>]*aria-current="page"', tools(review))
    assert re.search(r'id="nav-search"[^>]*aria-current="page"', review)
    assert 'href="/failures"' in client.get('/overview').text
    assert 'href="/overview"' in client.get('/failures').text
    state = client.get('/api/features').json()
    client.put('/api/features', json={'values': {'review': False, 'failures': False}, 'expected_revision': state['revision']})
    assert 'href="/candidates"' not in client.get('/knowledge').text
    assert 'href="/failures"' not in client.get('/overview').text
    assert client.get('/api/candidates').status_code == 403
    assert client.get('/api/failures').status_code == 403
