import json
from threading import Event, Thread
from datetime import datetime, timezone

from qq_digest.models import GroupConfig, NormalizedMessage
from tests.test_web import web_client  # noqa: F401


def enable_topics(client):
    client.post('/login', data={'password': 'password123'})
    state = client.get('/api/features').json()
    result = client.put('/api/features', json={
        'values': {'topics': True}, 'expected_revision': state['revision']})
    assert result.status_code == 200


def seed_report(client, tmp_path, gid=123, day='2026-10-04', title='测试项目部署'):
    archive = client.app.state.archive
    if gid != 123:
        archive.upsert_groups([GroupConfig(group_id=gid, name=f'群 {gid}')])
    stamp = datetime.fromisoformat(day + 'T04:00:00+00:00')
    archive.ingest([NormalizedMessage(msg_id='topic-source', group_id=gid,
        timestamp=stamp, collected_at=stamp, text=title + '进展')])
    payload = {'evidence_version': 1, 'main_topics': [
        {'topic': title, 'summary': '部署已经完成', 'message_ids': ['topic-source']}],
        'conclusions': [{'text': '可以上线', 'message_ids': ['topic-source']}],
        'open_questions': [{'text': '监控如何配置', 'message_ids': ['topic-source']}]}
    path = tmp_path / f'{gid}-{day}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    markdown = path.with_suffix('.md')
    markdown.write_text('# 部署', encoding='utf-8')
    return archive.record_report(group_id=gid, report_date=day,
        markdown_path=markdown, json_path=path, candidate_ids=[])


def test_topic_routes_require_login_before_parameter_validation(web_client):
    client, _, _ = web_client
    for path in ('/api/topics', '/api/topics/1',
                 '/api/topic-discussions/1/suggestions',
                 '/api/topic-discussions/1/sources/x'):
        assert client.get(path).status_code == 401
    assert client.post('/api/topics/refresh').status_code == 401
    assert client.patch('/api/topics/1', json={}).status_code == 401
    assert client.post('/api/topics/1/merge', json={}).status_code == 401
    assert client.patch('/api/topic-discussions/1', json={}).status_code == 401
    assert client.get('/topics', follow_redirects=False).headers['location'] == '/login'


def test_topics_disabled_by_default_and_reopening_preserves_index(web_client, tmp_path):
    client, _, _ = web_client
    client.post('/login', data={'password': 'password123'})
    assert client.get('/api/features').json()['values']['topics'] is False
    assert client.get('/api/topics').status_code == 403
    enable_topics(client)
    seed_report(client, tmp_path)
    assert client.post('/api/topics/refresh').status_code == 200
    item = client.get('/api/topics').json()['topics'][0]
    state = client.get('/api/features').json()
    client.put('/api/features', json={'values': {'topics': False}, 'expected_revision': state['revision']})
    assert client.get('/topics', follow_redirects=False).headers['location'].startswith('/settings')
    assert client.patch(f"/api/topics/{item['topic_id']}", json={}).status_code == 403
    enable_topics(client)
    assert client.get('/api/topics').json()['topics'][0]['topic_id'] == item['topic_id']


def test_topics_cross_group_timeline_mutation_and_verified_context(web_client, tmp_path):
    client, _, _ = web_client
    enable_topics(client)
    seed_report(client, tmp_path)
    seed_report(client, tmp_path, gid=456, day='2026-10-05')
    assert client.post('/api/topics/refresh').status_code == 200
    rows = client.get('/api/topics').json()['topics']
    assert len(rows) == 1 and rows[0]['discussion_count'] == 2
    tid = rows[0]['topic_id']
    detail = client.get(f'/api/topics/{tid}').json()
    assert {d['group_id'] for d in detail['discussions']} == {123, 456}
    d = detail['discussions'][0]
    source = client.get(f"/api/topic-discussions/{d['discussion_id']}/sources/topic-source")
    assert source.status_code == 200 and source.json()['anchor_id'] == 'topic-source'
    assert client.get(f"/api/topic-discussions/{d['discussion_id']}/sources/not-cited").status_code == 404
    assert client.get(f"/api/topic-discussions/{d['discussion_id']}/sources/topic-source?direction=bad").status_code == 422
    updated = client.patch(f'/api/topics/{tid}', json={'title': '发布跟进', 'expected_revision': detail['revision']})
    assert updated.status_code == 200 and updated.json()['title'] == '发布跟进'
    assert client.patch(f'/api/topics/{tid}', json={'title': '旧请求', 'expected_revision': detail['revision']}).status_code == 409
    assert client.post('/api/topics/refresh').status_code == 200
    assert client.get(f'/api/topics/{tid}').json()['title'] == '发布跟进'
    page = client.get('/topics').text
    assert 'id="nav-reports" aria-current="page"' in page
    assert 'id="topics-refresh"' in page


def test_topic_inputs_and_running_task_conflicts(web_client, tmp_path):
    client, _, _ = web_client
    enable_topics(client)
    seed_report(client, tmp_path)
    with client.app.state.operations.claim('daily'):
        assert client.post('/api/topics/refresh').status_code == 409
        assert client.get('/api/topics').status_code == 409
    assert client.get('/api/topics?date_from=bad').status_code == 422
    assert client.get('/api/topics?date_from=2026-10-05&date_to=2026-10-04').status_code == 422
    assert client.get('/api/topics?page=0').status_code == 422
    assert client.get('/api/topics?group_id=9999999999999999999999999999').status_code == 422
    assert client.get('/api/topics/999').status_code == 404
    assert client.patch('/api/topics/999', json={'title': ' ', 'expected_revision': 0}).status_code == 422
    assert client.post('/api/topics/refresh').status_code == 200
    item = client.get('/api/topics').json()['topics'][0]
    with client.app.state.operations.claim('backup'):
        assert client.patch(f"/api/topics/{item['topic_id']}", json={
            'title': '名称', 'expected_revision': item['revision']}).status_code == 409


def test_failed_topic_refresh_cannot_be_committed_by_feature_settings(web_client, tmp_path, monkeypatch):
    client, _, _ = web_client
    enable_topics(client)
    seed_report(client, tmp_path)
    assert client.post('/api/topics/refresh').status_code == 200
    archive = client.app.state.archive
    before = [tuple(row) for row in archive.connection.execute('SELECT * FROM topic_discussions')]
    path = tmp_path / '123-2026-10-04.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['main_topics'][0]['topic'] = '另一个具体项目部署'
    path.write_text(json.dumps(payload), encoding='utf-8')
    service = client.app.state.topic_tracking
    entered, release = Event(), Event()
    original = service._automatic_topic

    def fail_after_insert(title):
        original(title)
        entered.set()
        assert release.wait(5)
        raise OSError('synthetic failure')

    monkeypatch.setattr(service, '_automatic_topic', fail_after_insert)
    responses = []
    worker = Thread(target=lambda: responses.append(client.post('/api/topics/refresh')))
    worker.start()
    try:
        assert entered.wait(5)
        # A commit on the application's connection must not commit the worker.
        archive.connection.commit()
    finally:
        release.set()
        worker.join(10)
    assert responses[0].status_code == 503
    assert [tuple(row) for row in archive.connection.execute('SELECT * FROM topic_discussions')] == before
    assert archive.connection.execute('SELECT COUNT(*) FROM tracked_topics').fetchone()[0] == 1
