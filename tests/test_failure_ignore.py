import json
import sqlite3
from datetime import datetime, timezone

import pytest

from qq_digest.archive import Archive
from qq_digest.failure_center import FailureCenterService
from qq_digest.models import GroupConfig
from qq_digest.reminders import ReminderService
from tests.test_web import web_client


def failed(ar, error='导入失败', groups=None):
    job = ar.start_job('message_sync')
    ar.finish_job(job, 'failed', json.dumps(groups) if groups else error)
    return job


def select(item):
    return {'id': item['id'], 'fingerprint': item['fingerprint']}


def test_ignore_persists_restores_and_keeps_job_result(tmp_path):
    path = tmp_path / 'archive.sqlite'
    ar = Archive.open(path); job = failed(ar)
    service = FailureCenterService(ar); item = service.list_items()['items'][0]
    assert service.ignore_items([select(item)]) == {'changed': 1, 'skipped': 0}
    assert service.ignore_items([select(item)]) == {'changed': 0, 'skipped': 1}
    ar.connection.close(); ar = Archive.open(path); service = FailureCenterService(ar)
    result = service.list_items()
    assert result['counts']['ignored'] == 1 and result['counts']['active'] == 0
    assert result['items'][0]['status'] == 'ignored' and not result['items'][0]['action']
    assert ar.connection.execute('SELECT status FROM jobs WHERE job_id=?', (job,)).fetchone()[0] == 'failed'
    assert service.restore_items([select(result['items'][0])])['changed'] == 1
    assert service.list_items()['items'][0]['status'] == 'active'


def test_group_scope_bulk_snapshot_and_new_arrivals(tmp_path):
    ar = Archive.open(tmp_path / 'a.sqlite')
    failed(ar, groups=[{'group_id': 123, 'error': '甲'}, {'group_id': 456, 'error': '乙'}])
    service = FailureCenterService(ar); items = service.list_items()['items']
    selected = next(i for i in items if i['group_id'] == 123)
    new = failed(ar, '新异常')
    assert service.ignore_items([select(selected), select(selected)])['changed'] == 1
    result = service.list_items()
    assert next(i for i in result['items'] if i['group_id'] == 456)['status'] == 'active'
    assert next(i for i in result['items'] if i['job_id'] == new)['status'] == 'active'


def test_new_sync_occurrence_and_full_error_invalidate_ignore(tmp_path):
    ar = Archive.open(tmp_path / 'a.sqlite'); ar.upsert_groups([GroupConfig(group_id=123, name='甲')])
    ar.mark_sync_failure(group_id=123, status='adapter_incompatible', error='x' * 550 + 'A')
    service = FailureCenterService(ar); old = service.list_items()['items'][0]
    assert len(old['error']) == 500
    service.ignore_items([select(old)])
    ar.connection.execute("UPDATE sync_state SET error=? WHERE group_id=123", ('x' * 550 + 'B',)); ar.connection.commit()
    current = service.list_items()['items'][0]
    assert current['status'] == 'active' and current['fingerprint'] != old['fingerprint']
    assert service.restore_items([select(old)])['changed'] == 0
    assert service.ignore_items([select(old)])['changed'] == 0
    service.ignore_items([select(current)])
    ar.connection.execute("UPDATE sync_state SET updated_at='2026-10-08T00:00:00' WHERE group_id=123"); ar.connection.commit()
    assert service.list_items()['items'][0]['status'] == 'active'


def test_retrying_and_recovered_jobs_cannot_be_ignored(tmp_path):
    ar = Archive.open(tmp_path / 'a.sqlite'); job = failed(ar, groups=[{'group_id': 123, 'error': '错误'}])
    service = FailureCenterService(ar); old = select(service.list_items()['items'][0])
    retry = ar.start_job('message_sync', retry_of_job_id=job)
    assert service.list_items()['items'][0]['status'] == 'pending'
    assert service.ignore_items([old])['changed'] == 0
    ar.finish_job(retry, 'success', group_outcomes={123: 'success'})
    assert service.list_items()['items'][0]['status'] == 'recovered'
    assert service.ignore_items([old])['changed'] == 0


def test_recovery_wins_over_ignore(tmp_path):
    ar = Archive.open(tmp_path / 'a.sqlite'); job = failed(ar, groups=[{'group_id': 123, 'error': '错误'}])
    service = FailureCenterService(ar); old = select(service.list_items()['items'][0]); service.ignore_items([old])
    retry = ar.start_job('message_sync', retry_of_job_id=job)
    assert service.list_items()['items'][0]['status'] == 'pending'
    ar.finish_job(retry, 'success', group_outcomes={123: 'success'})
    assert service.list_items()['items'][0]['status'] == 'recovered'
    assert service.restore_items([old])['changed'] == 0


def test_batch_rollback_on_database_error(tmp_path):
    ar = Archive.open(tmp_path / 'a.sqlite'); failed(ar, '一'); failed(ar, '二')
    service = FailureCenterService(ar); items = [select(i) for i in service.list_items()['items']]
    ar.connection.executescript(f"""CREATE TRIGGER reject_ignore BEFORE INSERT ON failure_ignores
        WHEN NEW.source_id='{items[1]['id']}' BEGIN SELECT RAISE(ABORT,'test failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError): service.ignore_items(items)
    assert service.list_items()['counts']['ignored'] == 0


def test_notification_ignore_reminder_filter_and_pending_guard(tmp_path):
    ar = Archive.open(tmp_path / 'a.sqlite')
    send = ar.enqueue_notification(report_id=None, channel='qq_bot_private', recipient='synthetic', payload={})
    ar.mark_notification_retry(send, error='发送失败', next_attempt_at=datetime.now(timezone.utc), max_attempts=1)
    service = FailureCenterService(ar); old = select(service.list_items()['items'][0])
    assert len(ReminderService(ar)._failures()) == 1
    assert service.ignore_items([old])['changed'] == 1
    assert ReminderService(ar)._failures() == []
    assert service.restore_items([old])['changed'] == 1
    assert len(ReminderService(ar)._failures()) == 1
    ar.retry_failed_notification(send)
    assert service.list_items()['items'][0]['status'] == 'pending'
    assert service.ignore_items([old])['changed'] == 0


def test_ignore_routes_login_validation_and_restore(web_client):
    client, _, _ = web_client; ar = client.app.state.archive; failed(ar)
    item = select(FailureCenterService(ar).list_items()['items'][0])
    assert client.post('/api/failures/ignore', json={'items': [item]}).status_code == 401
    assert client.post('/api/failures/restore', json={'items': [item]}).status_code == 401
    client.post('/login', data={'password': 'password123'})
    assert client.post('/api/failures/ignore', json={'items': []}).status_code == 422
    assert client.post('/api/failures/ignore', json={'items': [item] * 401}).status_code == 422
    assert client.post('/api/failures/ignore', json={'items': [{'id': item['id'], 'fingerprint': 'bad'}]}).status_code == 422
    assert client.post('/api/failures/ignore', json={'items': [item]}).json() == {'changed': 1, 'skipped': 0}
    result = client.get('/api/failures').json()
    assert result['counts']['ignored'] == 1
    assert client.post('/api/failures/restore', json={'items': [item]}).json() == {'changed': 1, 'skipped': 0}
    state = client.get('/api/features').json()
    assert client.put('/api/features', json={'values': {'failures': False}, 'expected_revision': state['revision']}).status_code == 200
    assert client.post('/api/failures/ignore', json={'items': [item]}).status_code == 403
    assert client.post('/api/failures/restore', json={'items': [item]}).status_code == 403
