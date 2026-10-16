"""Synthetic regressions for manual publication identity and legacy review actions."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from tests.test_message_browsing import browsing_client, add_message, login


def source_payload():
    return dict(group_id=11, msg_id='audit-source', item_type='resource',
                title='Original resource', link='https://original.example', reason='Original reason')


def prepare_source(client, archive):
    add_message(archive, 'audit-source', datetime(2026, 10, 4, 15, tzinfo=timezone.utc),
                text='Original source body\nComplete steps')
    login(client)


def save_source(client):
    response = client.post('/api/knowledge/from-message', json=source_payload())
    assert response.status_code == 200, response.text
    return response.json()


def undo_and_edit(client, candidate_id, **changes):
    assert client.post(f'/api/candidates/{candidate_id}/undo').status_code == 200
    current = client.app.state.candidates.get(candidate_id)
    fields = {key: getattr(current, key) for key in
              ('candidate_type', 'title', 'link', 'content', 'reason', 'excerpt')}
    response = client.patch(f'/api/candidates/{candidate_id}',
                            json=fields | changes | {'expected_revision': current.revision})
    assert response.status_code == 200, response.text
    return client.app.state.candidates.get(candidate_id).model_dump()


@pytest.mark.parametrize('changes', [
    dict(candidate_type='experience', title='Edited experience', link='https://edited.example',
         content='Human body', reason='Human reason', excerpt='Human excerpt'),
    dict(content='Human body'), dict(reason='Human reason'), dict(excerpt='Human excerpt'),
])
def test_resaving_source_preserves_edited_pending_candidate(browsing_client, changes):
    client, archive, config = browsing_client
    prepare_source(client, archive)
    old_id = save_source(client)['item_id']
    edited = undo_and_edit(client, old_id, **changes)
    saved = save_source(client)
    assert saved['item_id'] != old_id
    assert client.app.state.candidates.get(old_id).model_dump() == edited
    detail = client.get(f"/api/knowledge/{saved['item_id']}").json()
    assert (detail['item_type'], detail['title'], detail['link']) == (
        'resource', 'Original resource', 'https://original.example')
    assert detail['content'] == 'Original source body\nComplete steps'
    assert detail['reason'] == 'Original reason'
    assert save_source(client) == {'item_id': saved['item_id'], 'reused': True}
    assert (config.knowledge_dir / 'resources.md').read_text(encoding='utf-8').count('条目 ID：') == 1


def test_unchanged_undone_source_reuses_candidate(browsing_client):
    client, archive, _ = browsing_client
    prepare_source(client, archive)
    old_id = save_source(client)['item_id']
    assert client.post(f'/api/candidates/{old_id}/undo').status_code == 200
    assert save_source(client)['item_id'] == old_id
    assert archive.connection.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 1


@pytest.mark.parametrize('existing_target', [False, True])
def test_resave_write_failure_restores_actual_target_and_edited_candidate(
        browsing_client, monkeypatch, existing_target):
    client, archive, config = browsing_client
    prepare_source(client, archive)
    old_id = save_source(client)['item_id']
    edited = undo_and_edit(client, old_id, candidate_type='experience', content='Human body')
    resource = config.knowledge_dir / 'resources.md'
    if existing_target:
        resource.write_text('Existing resources\n', encoding='utf-8')
    else:
        resource.unlink()
    experience = config.knowledge_dir / 'experiences.md'
    experience.write_text('Existing experiences\n', encoding='utf-8')
    originals = {path: path.read_bytes() if path.exists() else None for path in (resource, experience)}
    real_write = client.app.state.knowledge.write
    def fail_after_write(*args, **kwargs):
        real_write(*args, **kwargs)
        raise OSError('synthetic post-write failure')
    monkeypatch.setattr(client.app.state.knowledge, 'write', fail_after_write)
    response = client.post('/api/knowledge/from-message', json=source_payload())
    assert response.status_code == 503
    assert {path: path.read_bytes() if path.exists() else None for path in originals} == originals
    assert client.app.state.candidates.get(old_id).model_dump() == edited
    assert archive.connection.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 1
    assert archive.connection.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0] == 0


@pytest.mark.parametrize('status, expected', [('pending', 303), ('later', 303),
                                             ('confirmed', 409), ('ignored', 409)])
def test_legacy_ignore_respects_review_status_and_keeps_publication(browsing_client, status, expected):
    client, archive, config = browsing_client
    prepare_source(client, archive)
    candidate_id = save_source(client)['item_id']
    if status != 'confirmed':
        assert client.post(f'/api/candidates/{candidate_id}/undo').status_code == 200
        client.app.state.candidates.update_status(candidate_id, status)
    before = client.app.state.candidates.get(candidate_id).model_dump()
    path = config.knowledge_dir / 'resources.md'
    original = path.read_bytes()
    response = client.post(f'/candidates/{candidate_id}/ignore', data={'reason': 'Duplicate'},
                           follow_redirects=False)
    assert response.status_code == expected
    current = client.app.state.candidates.get(candidate_id)
    if expected == 303:
        assert response.headers['location'] == '/candidates'
        assert (current.status, current.ignore_reason) == ('ignored', 'Duplicate')
        assert current.revision > before['revision']
    else:
        assert current.model_dump() == before
    assert path.read_bytes() == original
    assert archive.connection.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0] == int(status == 'confirmed')
    if status == 'confirmed':
        assert client.get(f'/api/knowledge/{candidate_id}').status_code == 200


def test_legacy_ignore_missing_candidate_returns_404(browsing_client):
    client, _, _ = browsing_client
    login(client)
    assert client.post('/candidates/999/ignore', follow_redirects=False).status_code == 404


@pytest.mark.parametrize('active', ['manual_summary', 'knowledge_mutation'])
def test_legacy_ignore_obeys_shared_mutation_guard(browsing_client, active):
    client, archive, _ = browsing_client
    prepare_source(client, archive)
    candidate_id = save_source(client)['item_id']
    assert client.post(f'/api/candidates/{candidate_id}/undo').status_code == 200
    before = client.app.state.candidates.get(candidate_id).model_dump()
    with client.app.state.operations.claim(active):
        response = client.post(f'/candidates/{candidate_id}/ignore', follow_redirects=False)
    assert response.status_code == 409
    assert client.app.state.candidates.get(candidate_id).model_dump() == before


def test_ignore_during_publication_is_busy_and_does_not_hide_written_knowledge(browsing_client, monkeypatch):
    client, archive, _ = browsing_client
    prepare_source(client, archive)
    candidate_id = save_source(client)['item_id']
    assert client.post(f'/api/candidates/{candidate_id}/undo').status_code == 200
    other = TestClient(client.app)
    login(other)
    real_write = client.app.state.knowledge.write
    attempts = []
    def write_and_attempt_ignore(*args, **kwargs):
        real_write(*args, **kwargs)
        attempts.append(other.post(f'/candidates/{candidate_id}/ignore', follow_redirects=False).status_code)
    monkeypatch.setattr(client.app.state.knowledge, 'write', write_and_attempt_ignore)
    try:
        assert client.post(f'/api/candidates/{candidate_id}/confirm').status_code == 200
        assert attempts == [409]
        assert client.get(f'/api/knowledge/{candidate_id}').status_code == 200
    finally:
        other.close()


def test_legacy_ignore_sqlite_failure_returns_retryable_503(browsing_client, monkeypatch):
    client, _, _ = browsing_client
    login(client)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(client.app.state.candidates, 'ignore', unavailable)
    response = client.post('/candidates/999/ignore', follow_redirects=False)
    assert response.status_code == 503
    assert '稍后重试' in response.json()['detail']


def test_concurrent_ignore_allows_only_one_status_transition(browsing_client):
    client, archive, config = browsing_client
    prepare_source(client, archive)
    candidate_id = save_source(client)['item_id']
    assert client.post(f'/api/candidates/{candidate_id}/undo').status_code == 200
    workers = [Archive.open(config.archive_path) for _ in range(2)]
    barrier = Barrier(2)
    def ignore(worker):
        barrier.wait(timeout=10)
        try:
            CandidateService(worker).ignore(candidate_id, 'Duplicate')
            return 'ignored'
        except ValueError:
            return 'conflict'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(ignore, workers)) == ['conflict', 'ignored']
    finally:
        for worker in workers:
            worker.close()
