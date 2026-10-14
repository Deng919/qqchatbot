"""Synthetic regressions for reviewed candidates and isolated task decisions."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig
from qq_digest.reminders import ReminderService
from qq_digest.task_inbox import TaskInboxService
from qq_digest.web.task_routes import add_task_routes
from tests.test_candidates import candidate_kwargs
from tests.test_message_browsing import browsing_client, add_message, login
from tests.test_reminders import rule, NOW, message, candidate, task


@pytest.fixture
def candidates(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    archive.upsert_groups([GroupConfig(group_id=123, name='测试群')])
    yield CandidateService(archive)
    archive.close()


def edit(service, ident, **changes):
    fields = {key: getattr(service.get(ident), key) for key in
              ('candidate_type', 'title', 'link', 'content', 'reason', 'excerpt')}
    return service.update_details(ident, **(fields | changes))


@pytest.mark.parametrize('title', ['站点', '人工改名'])
def test_regeneration_preserves_human_edits_and_original_identity(candidates, title):
    ident = candidates.create(**candidate_kwargs())
    edit(candidates, ident, title=title, content='人工正文', reason='人工理由')
    regenerated = candidates.create(**(candidate_kwargs() | {'content': 'AI replacement'}))
    assert regenerated == ident
    current = candidates.get(ident)
    assert (current.title, current.content, current.reason) == (title, '人工正文', '人工理由')
    assert current.human_edited


@pytest.mark.parametrize('unreferenced', [False, True])
def test_garbage_collection_preserves_human_edited_pending(candidates, unreferenced):
    edited = candidates.create(**candidate_kwargs())
    untouched = candidates.create(**(candidate_kwargs() | {'title': 'untouched'}))
    edit(candidates, edited, content='human')
    if unreferenced:
        with candidates.archive.transaction():
            count = candidates.archive.delete_unreferenced_pending_candidates_in_transaction([edited, untouched])
    else:
        count = candidates.archive.delete_pending_candidates([edited, untouched])
    assert count == 1
    assert candidates.get(edited).content == 'human'


def test_candidate_stale_edit_is_rejected_without_overwriting(candidates):
    ident = candidates.create(**candidate_kwargs())
    revision = candidates.get(ident).revision
    edit(candidates, ident, content='first editor', expected_revision=revision)
    with pytest.raises(ValueError, match='已被修改'):
        edit(candidates, ident, content='stale editor', expected_revision=revision)
    assert candidates.get(ident).content == 'first editor'


def test_candidate_ai_refresh_and_status_invalidate_edit_revision(candidates):
    ident = candidates.create(**candidate_kwargs())
    old = candidates.get(ident).revision
    candidates.create(**(candidate_kwargs() | {'content': 'AI refresh'}))
    assert candidates.get(ident).revision > old
    revision = candidates.get(ident).revision
    candidates.update_status(ident, 'later')
    assert candidates.get(ident).revision > revision


def test_candidate_pages_and_count_do_not_truncate_at_500(candidates):
    with candidates.archive.transaction():
        for index in range(507):
            candidates.create_in_transaction(**(candidate_kwargs() | {'title': f'site {index}'}))
    page = candidates.query_page(status='pending', q='site', page=26, page_size=20)
    assert (page['total'], page['page'], page['page_size'], len(page['items'])) == (507, 26, 20, 7)
    assert candidates.count(status='pending', q='site') == 507
    assert len(candidates.query(status='pending', limit=200)) == 200
    with pytest.raises(ValueError):
        candidates.query_page(group_ids=[], page=1)
    assert candidates.query_page(group_id=999)['total'] == 0


def test_schema_migrates_legacy_candidate_and_group_columns(tmp_path):
    archive = Archive.open(tmp_path / 'old.sqlite')
    service = CandidateService(archive)
    ident = service.create(**candidate_kwargs())
    edit(service, ident, content='pre-migration human edit')
    archive.upsert_groups([GroupConfig(group_id=123, name='测试群')])
    archive.connection.execute('DROP INDEX IF EXISTS idx_candidates_generation_key')
    for table, columns in [('candidates', ('generation_key', 'human_edited', 'revision')), ('groups', ('revision',))]:
        for column in columns:
            archive.connection.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
    archive.connection.commit()
    archive.close()
    reopened = Archive.open(tmp_path / 'old.sqlite')
    try:
        assert CandidateService(reopened).get(ident).revision == 0
        assert CandidateService(reopened).get(ident).human_edited
        assert reopened.all_groups()[0].revision == 0
        assert CandidateService(reopened).create(**candidate_kwargs()) == ident
        assert CandidateService(reopened).get(ident).content == 'pre-migration human edit'
    finally:
        reopened.close()


def test_reminders_link_to_precise_source(candidates):
    service = ReminderService(candidates.archive)
    msg_id = 'a/?&中文'
    message(service, msg_id)
    cid = candidate(service)
    tid = task(service)
    expected = [('keyword', '/search', {'group_id': ['123'], 'msg_id': [msg_id]}),
                ('resource', '/candidates', {'candidate_id': [str(cid)]}),
                ('task_due', '/tasks', {'task_id': [str(tid)]})]
    for kind, path, query in expected:
        items = service.preview(rule(kind), now=NOW)['items']
        source = next(item for item in items if parse_qs(urlsplit(item['source_url']).query) == query)
        assert urlsplit(source['source_url']).path == path


@pytest.mark.parametrize('kind', ['resource', 'task_due'])
def test_historical_reminder_urls_resolve_stored_target(candidates, kind):
    service = ReminderService(candidates.archive)
    service.save_rule(rule(kind), now=NOW)
    ident = candidate(service) if kind == 'resource' else task(service)
    service.scan(now=NOW)
    base = '/candidates' if kind == 'resource' else '/tasks'
    key = 'candidate_id' if kind == 'resource' else 'task_id'
    candidates.archive.connection.execute('UPDATE reminder_events SET source_url=?', (base,))
    candidates.archive.connection.commit()
    event = service.list_events()['items'][0]
    assert event['source_url'] == f'{base}?{key}={ident}'
    assert event['source_available']


def test_legacy_message_reminder_cannot_guess_recycled_rowid_identity(candidates):
    service = ReminderService(candidates.archive)
    service.save_rule(rule(), now=NOW)
    message(service, 'original')
    service.scan(now=NOW)
    c = candidates.archive.connection
    rowid = c.execute("SELECT rowid FROM messages WHERE msg_id='original'").fetchone()[0]
    c.execute("UPDATE reminder_events SET source_ref=?,source_url='/search?group_id=123'", (str(rowid),))
    c.commit()
    event = service.list_events()['items'][0]
    assert event['source_url'] == ''
    assert not event['source_available']
    assert '身份' in event['source_unavailable_reason']


def test_new_keyword_reminder_persists_exact_message_identity(candidates):
    service = ReminderService(candidates.archive)
    service.save_rule(rule(), now=NOW)
    message(service, 'original')
    service.scan(now=NOW)
    assert json.loads(candidates.archive.connection.execute('SELECT source_ref FROM reminder_events').fetchone()[0]) == [123, 'original']
    assert service.list_events()['items'][0]['source_available']
    candidates.archive.connection.execute("DELETE FROM messages WHERE msg_id='original'")
    candidates.archive.connection.commit()
    event = service.list_events()['items'][0]
    assert event['source_url'] == ''
    assert not event['source_available']


def test_concurrent_ignore_decisions_return_business_error(tmp_path, monkeypatch):
    from tests.test_task_inbox import setup_inbox, add_report, NOW as task_now
    archive, inbox = setup_inbox(tmp_path)
    add_report(archive, tmp_path)
    key = inbox.suggestions(now=task_now)[0]['key']
    workers = [Archive.open(tmp_path / 'archive.sqlite') for _ in range(2)]
    services = [TaskInboxService(worker, timezone_name='Asia/Shanghai') for worker in workers]
    barrier = Barrier(2)
    original = TaskInboxService.suggestions
    def simultaneous(self, **kwargs):
        result = original(self, **kwargs)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(TaskInboxService, 'suggestions', simultaneous)
    def ignore(service):
        try:
            service.decide(key, action='ignore', now=task_now)
            return 'ignored'
        except ValueError as error:
            return str(error)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(ignore, services))
        assert results.count('ignored') == 1
        assert any('已处理' in result for result in results)
        assert archive.connection.execute('SELECT COUNT(*) FROM task_suggestion_decisions').fetchone()[0] == 1
        assert archive.connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    finally:
        for worker in workers:
            worker.close()
        archive.close()


def test_task_decision_storage_busy_is_retryable(candidates, monkeypatch):
    def busy(*args, **kwargs):
        error = sqlite3.OperationalError('database is locked')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error
    monkeypatch.setattr(TaskInboxService, 'decide', busy)
    app = FastAPI()
    add_task_routes(app, archive=candidates.archive, config=None, templates=None, require_login=lambda request: None)
    with TestClient(app) as client:
        response = client.post('/api/tasks/suggestions/synthetic/decision', json={'action': 'ignore'})
    assert response.status_code == 503
    assert '稍后重试' in response.json()['detail']


@pytest.mark.parametrize('config_none', [False, True])
def test_concurrent_task_confirm_uses_private_worker_connection(browsing_client, monkeypatch, tmp_path, config_none):
    client, archive, cfg = browsing_client
    timezone_name = 'Asia/Shanghai' if config_none else cfg.summary.timezone
    if config_none:
        app = FastAPI()
        add_task_routes(app, archive=archive, config=None, templates=None, require_login=lambda request: None)
        client = TestClient(app)
    else:
        login(client)
    other = TestClient(client.app)
    if not config_none:
        login(other)
    now = datetime.now(timezone.utc)
    add_message(archive, 'task-source', now)
    md = tmp_path / 'report.md'; md.write_text('synthetic', encoding='utf-8')
    jp = tmp_path / 'report.json'; jp.write_text(json.dumps({'evidence_version': 1, 'tasks': [
        {'owner': '', 'description': 'suggestion', 'message_ids': ['task-source']}]}), encoding='utf-8')
    archive.record_report(group_id=11, report_date=now.astimezone(ZoneInfo(timezone_name)).date().isoformat(), markdown_path=md, json_path=jp, candidate_ids=[])
    key = TaskInboxService(archive, timezone_name=timezone_name).suggestions()[0]['key']
    original = TaskInboxService.decide
    barrier = Barrier(2)
    connections = []
    def synchronized(self, *args, **kwargs):
        connections.append(self.archive.connection)
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(TaskInboxService, 'decide', synchronized)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(c.post, f'/api/tasks/suggestions/{key}/decision', json={'action': 'confirm', 'title': 'accepted'}) for c in (client, other)]
            results = [f.result(timeout=20) for f in futures]
        assert all(connection is not archive.connection for connection in connections)
        assert connections[0] is not connections[1]
        assert sorted(r.status_code for r in results) == [200, 400]
        assert archive.connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
        assert archive.connection.execute('SELECT COUNT(*) FROM task_suggestion_decisions').fetchone()[0] == 1
    finally:
        other.close()
        if config_none:
            client.close()
