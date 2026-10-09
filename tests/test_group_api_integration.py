from tests.test_web import web_client


def test_batch_attachment_route_precedes_dynamic_single_group_route(web_client, monkeypatch):
    from qq_digest.attachments import AttachmentService
    client, _, _ = web_client
    monkeypatch.setattr(AttachmentService, 'prepare', lambda *args, **kwargs: None)
    client.post('/login', data={'password': 'password123'})
    response = client.post('/api/groups/batch/attachments/read', json={'group_ids': [123]})
    assert response.status_code == 200, response.text
    assert response.json()['results'][0]['group_id'] == 123


def test_candidate_scope_is_integrated_in_main_app(web_client):
    client, _, _ = web_client
    client.post('/login', data={'password': 'password123'})
    assert client.post('/api/groups/batch/add', json={'groups': [{'group_id': 456, 'name': '另一个群'}]}).status_code == 200
    candidates = client.app.state.candidates
    candidates.create(group_id=456, created_date='2026-08-24', candidate_type='experience', title='其他群经验', reason='合成数据', message_ids=['synthetic-message'])
    response = client.get('/api/candidates?group_ids=123')
    assert response.status_code == 200
    assert {item['group_id'] for item in response.json()['candidates']} == {123}
    assert client.get('/api/candidates?group_id=456&group_ids=123').status_code == 422
