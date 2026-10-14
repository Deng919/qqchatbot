import pytest

from tests.test_task_inbox import NOW, setup_inbox
from tests.test_web import web_client
from qq_digest.models import NormalizedMessage
from qq_digest.task_inbox import TaskConflictError


def test_task_revision_outside_sqlite_range_is_not_server_failure(web_client):
    client = web_client[0]
    client.post('/login', data={'password': 'password123'})
    response = client.patch('/api/tasks/1', json={
        'title': 'draft', 'owner': '', 'due_date': None,
        'status': 'open', 'expected_revision': 2**63,
    })
    assert response.status_code == 422


@pytest.mark.parametrize('task_id', [2**63, -(2**63)-1])
@pytest.mark.parametrize('payload', [
    {'status': 'completed'},
    {'title': 'draft', 'status': 'open', 'expected_revision': 0},
])
def test_task_path_outside_sqlite_range_is_not_server_failure(web_client, task_id, payload):
    client = web_client[0]
    client.post('/login', data={'password': 'password123'})
    response = client.patch(f'/api/tasks/{task_id}', json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize('group_id', [2**63, -(2**63)-1])
def test_message_task_group_outside_sqlite_range_is_not_server_failure(web_client, group_id):
    client = web_client[0]
    client.post('/login', data={'password': 'password123'})
    response = client.post('/api/tasks/from-message', json={
        'group_id': group_id, 'msg_id': 'source', 'title': 'draft',
    })
    assert response.status_code == 422


@pytest.mark.parametrize('revision', [2**63, -1, True, 1.0, '0'])
def test_direct_task_edit_rejects_invalid_revision(tmp_path, revision):
    archive, inbox = setup_inbox(tmp_path)
    try:
        task = inbox.create_from_message(group_id=123, msg_id='m1', title='original', now=NOW)
        with pytest.raises(ValueError, match='版本'):
            inbox.update_task(task['task_id'], title='draft', expected_revision=revision, now=NOW)
        assert inbox.list_tasks(status='all', now=NOW)['items'] == [task]
    finally:
        archive.close()


@pytest.mark.parametrize('entity_id', [-(2**63), 2**63-1])
def test_sqlite_identifier_boundaries_keep_missing_entity_errors(web_client, entity_id):
    client = web_client[0]
    client.post('/login', data={'password': 'password123'})
    assert client.patch(f'/api/tasks/{entity_id}', json={'status': 'completed'}).status_code == 400
    assert client.post('/api/tasks/from-message', json={
        'group_id': entity_id, 'msg_id': 'missing', 'title': 'draft',
    }).status_code == 400


@pytest.mark.parametrize('group_id', [123, '123', 123.0])
def test_valid_message_task_and_edits_keep_integer_conversion_compatibility(web_client, group_id):
    client = web_client[0]
    client.app.state.archive.ingest([NormalizedMessage(
        msg_id='source', group_id=123, timestamp=NOW, collected_at=NOW, text='source',
    )])
    client.post('/login', data={'password': 'password123'})
    created = client.post('/api/tasks/from-message', json={
        'group_id': group_id, 'msg_id': 'source', 'title': 'original',
    })
    assert created.status_code == 200
    task = created.json()['task']
    maximum = client.patch(f"/api/tasks/{task['task_id']}", json={
        'title': 'draft', 'status': 'open', 'expected_revision': 2**63-1,
    })
    assert maximum.status_code == 409
    assert client.get('/api/tasks').json()['items'] == [task]
    edited = client.patch(f"/api/tasks/{task['task_id']}", json={
        'title': 'edited', 'status': 'open', 'expected_revision': task['revision'],
    })
    assert edited.status_code == 200
    assert edited.json()['task']['revision'] == 1
    completed = client.patch(f"/api/tasks/{task['task_id']}", json={'status': 'completed'})
    assert completed.status_code == 200
    assert completed.json()['task']['title'] == 'edited'
    assert completed.json()['task']['revision'] == 2


def test_direct_task_edit_accepts_sqlite_revision_maximum_as_conflict(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    try:
        task = inbox.create_from_message(group_id=123, msg_id='m1', title='original', now=NOW)
        with pytest.raises(TaskConflictError):
            inbox.update_task(task['task_id'], title='draft', expected_revision=2**63-1, now=NOW)
        assert inbox.list_tasks(status='all', now=NOW)['items'] == [task]
    finally:
        archive.close()
