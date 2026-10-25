"""Retired features stay unavailable when old archives contain enabled flags."""
import json

import pytest

from qq_digest.features import FeatureService
from tests.test_reminders import NOW, message, rule, service
from tests.test_web import web_client


def test_old_task_topic_flags_are_not_exposed_or_editable(service):
    connection = service.connection
    connection.execute("UPDATE feature_settings SET payload=? WHERE singleton=1",
                       (json.dumps({'tasks': True, 'topics': True}),))
    connection.commit()
    features = FeatureService(service.archive)
    state = features.snapshot()
    assert not {'tasks', 'topics'} & state['values'].keys()
    assert not {'tasks', 'topics'} & {item['key'] for item in state['catalog']}
    for key in ('tasks', 'topics'):
        with pytest.raises(ValueError):
            features.update({key: True}, state['revision'])


@pytest.mark.parametrize('logged_in', [False, True])
@pytest.mark.parametrize('method,path', [
    ('get', '/tasks'), ('get', '/topics'), ('get', '/api/tasks'),
    ('get', '/api/tasks/suggestions'), ('post', '/api/tasks/from-message'),
    ('patch', '/api/tasks/1'), ('post', '/api/tasks/suggestions/old/decision'),
    ('get', '/api/topics'), ('post', '/api/topics/refresh'),
    ('get', '/api/topics/1'), ('patch', '/api/topics/1'),
    ('get', '/api/topic-discussions'),
])
def test_retired_routes_are_absent_even_with_legacy_flags(web_client, logged_in, method, path):
    client = web_client[0]
    connection = client.app.state.archive.connection
    connection.execute("UPDATE feature_settings SET payload=? WHERE singleton=1",
                       (json.dumps({'tasks': True, 'topics': True}),))
    connection.commit()
    if logged_in:
        client.post('/login', data={'password': 'password123'})
    assert getattr(client, method)(path, follow_redirects=False).status_code == 404


def task(service, due="2026-10-05"):
    message(service, "tasksource")
    c = service.archive.connection
    cur = c.execute("""INSERT INTO tasks(group_id,title,due_date,source_ids,primary_source_id,created_at,updated_at)
        VALUES(123,'完成任务',?,'["tasksource"]','tasksource',?,?)""", (due, NOW.isoformat(), NOW.isoformat()))
    c.commit()
    return cur.lastrowid



def legacy_due_rule(service):
    ident = task(service)
    cursor = service.connection.execute(
        "INSERT INTO reminder_rules(values_json,failure_baseline) VALUES(?,?)",
        (json.dumps(rule('task_due')), NOW.isoformat()))
    rule_id = cursor.lastrowid
    service.connection.commit()
    return rule_id, ident


def legacy_due_event(service, rule_id, ident, in_app='queued', windows='queued'):
    cursor = service.connection.execute("""INSERT INTO reminder_events(
        rule_id,event_key,kind,rule_name,title,body,group_id,group_name,
        source_url,source_ref,created_at,in_app_status,windows_status)
        VALUES(?,'legacy','task_due','旧规则','完成任务','历史提醒',123,'测试群',?,?,?,?,?)""",
        (rule_id, f'/tasks?task_id={ident}', json.dumps([ident, '2026-10-05']),
         NOW.isoformat(), in_app, windows))
    service.connection.commit()
    return cursor.lastrowid


def test_due_rules_cannot_be_created_or_previewed(service):
    for action in (service.save_rule, service.preview):
        with pytest.raises(ValueError):
            action(rule('task_due'), now=NOW)


def test_legacy_due_rules_are_hidden_and_never_scanned(service):
    rule_id, ident = legacy_due_rule(service)
    assert service.list_rules()['rules'] == []
    assert service.scan(now=NOW)['created'] == 0
    assert service.connection.execute('SELECT title FROM tasks WHERE task_id=?', (ident,)).fetchone()[0] == '完成任务'
    assert service.connection.execute('SELECT deleted FROM reminder_rules WHERE rule_id=?', (rule_id,)).fetchone()[0] == 0


@pytest.mark.parametrize('windows', ['queued', 'failed'])
def test_legacy_queued_due_reminders_are_canceled_before_delivery(service, windows):
    rule_id, ident = legacy_due_rule(service)
    event_id = legacy_due_event(service, rule_id, ident, windows=windows)
    delivered = []
    assert service.dispatch_windows(lambda *args: delivered.append(args) or True, now=NOW)['sent'] == 0
    assert delivered == []
    row = service.connection.execute('SELECT * FROM reminder_events WHERE event_id=?', (event_id,)).fetchone()
    assert row['in_app_status'] == row['windows_status'] == 'canceled'
    assert service.list_events()['queued'] == 0


def test_legacy_published_due_reminders_have_no_dead_source_or_retry(service):
    rule_id, ident = legacy_due_rule(service)
    event_id = legacy_due_event(service, rule_id, ident, in_app='published', windows='unknown')
    event = service.list_events()['items'][0]
    assert event['source_url'] == '' and event['source_available'] is False
    assert event['source_unavailable_reason']
    with pytest.raises(ValueError):
        service.retry_windows(event_id, now=NOW)
    assert service.mark_read(event_id)['read'] is True


@pytest.mark.parametrize('path', ['/api/reminder-rules', '/api/reminder-rules/preview'])
def test_due_rule_api_rejects_retired_kind(web_client, path):
    client = web_client[0]
    client.post('/login', data={'password': 'password123'})
    state = client.get('/api/features').json()
    client.put('/api/features', json={'values': {'reminders': True}, 'expected_revision': state['revision']})
    assert client.post(path, json=rule('task_due')).status_code == 422
