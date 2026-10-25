"""Synthetic regressions for reviewed candidates and reminder sources."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig
from qq_digest.reminders import ReminderService
from tests.test_candidates import candidate_kwargs
from tests.test_reminders import rule, NOW, message, candidate


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
    expected = [('keyword', '/search', {'group_id': ['123'], 'msg_id': [msg_id]}),
                ('resource', '/candidates', {'candidate_id': [str(cid)]})]
    for kind, path, query in expected:
        items = service.preview(rule(kind), now=NOW)['items']
        source = next(item for item in items if parse_qs(urlsplit(item['source_url']).query) == query)
        assert urlsplit(source['source_url']).path == path


def test_historical_resource_reminder_urls_resolve_stored_target(candidates):
    service = ReminderService(candidates.archive)
    service.save_rule(rule("resource"), now=NOW)
    ident = candidate(service)
    service.scan(now=NOW)
    base = '/candidates'
    key = 'candidate_id'
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
