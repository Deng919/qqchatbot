import json
from datetime import datetime, timezone

import pytest

from qq_digest.archive import Archive
from qq_digest.archive_qa import ArchiveQAService
from qq_digest.candidates import CandidateService
from qq_digest.knowledge_library import KnowledgeLibrary
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.search import search_archive


@pytest.fixture
def seeded(tmp_path):
    ar = Archive.open(tmp_path / 'multi-groups.sqlite')
    candidates = CandidateService(ar)
    ar.upsert_groups([GroupConfig(group_id=g, name=f'群{g}') for g in (11, 22, 33)])
    for group in (11, 22, 33):
        for number in range(12):
            stamp = datetime(2026, 10, 6, number, tzinfo=timezone.utc)
            mid = str(number)
            ar.ingest([NormalizedMessage(msg_id=mid, group_id=group, text=f'配置方案{group}',
                                        timestamp=stamp, collected_at=stamp)])
            cid = candidates.create(group_id=group, created_date='2026-10-06',
                candidate_type='experience', title=f'配置{group}-{number}', reason='配置', message_ids=[mid])
            candidates.confirm(cid)
            ar.connection.execute('INSERT INTO knowledge_items VALUES (?,?,?,?)',
                                  (str(cid), cid, 'unused.md', stamp.isoformat()))
        path = tmp_path / f'{group}.md'
        path.write_text('配置报告', encoding='utf-8')
        payload = {'main_topics': [{'topic': f'配置项目{group}部署', 'summary': '配置进展', 'message_ids': ['1']}]}
        json_path = path.with_suffix('.json')
        json_path.write_text(json.dumps(payload), encoding='utf-8')
        ar.record_report(group_id=group, report_date='2026-10-06',
            markdown_path=path, json_path=json_path, candidate_ids=[])
    ar.connection.commit()
    yield ar
    ar.close()


@pytest.mark.parametrize(('kind', 'total'), [('message', 24), ('knowledge', 24), ('report', 2), ('all', 50)])
def test_search_scope_precedes_count_order_and_pagination(seeded, kind, total):
    pages = [search_archive(seeded, query='配置', kind=kind, group_ids=[11, 33, 11],
                            page=p, page_size=7) for p in range(1, (total+6)//7+1)]
    assert {p['total'] for p in pages} == {total}
    items = [item for p in pages for item in p['results']]
    assert len(items) == total
    assert {item['group_id'] for item in items} == {11, 33}
    assert len({(i['kind'], i['id'], i['group_id']) for i in items}) == total


def test_candidate_and_knowledge_scope_precedes_limits(seeded):
    candidates = CandidateService(seeded)
    result = candidates.query(status='all', group_ids=[11, 33], limit=15)
    assert len(result) == 15 and {c.group_id for c in result} <= {11, 33}
    for c in result:
        candidates.update_status(c.candidate_id, 'pending')
    assert {c.group_id for c in candidates.pending(group_ids=[11, 33])} <= {11, 33}
    # Restore publication state for the knowledge listing.
    for c in result:
        candidates.confirm(c.candidate_id)
    library = KnowledgeLibrary(seeded, None)
    first = library.list_items(group_ids=[11, 33])
    second = library.list_items(group_ids=[11, 33], page=2)
    assert first['total'] == second['total'] == 24
    assert len(first['items']) == 20 and len(second['items']) == 4
    assert {i['group_id'] for i in first['items'] + second['items']} == {11, 33}




def test_qa_preview_freezes_selected_scope_and_sources(seeded):
    service = ArchiveQAService(seeded)
    groups = [11, 33]
    data = service.preview(owner='owner', question='配置方案？', query='配置', group_ids=groups,
                           date_from='2026-10-06', date_to='2026-10-07')
    assert data['scope']['group_ids'] == [11, 33]
    assert data['matched_count'] == 24
    assert {m['group_id'] for m in data['messages']} == {11, 33}
    groups.append(22)
    data['scope']['group_ids'].append(22)
    class AI:
        def chat(self, messages):
            prompt = json.loads(messages[1]['content'])
            assert prompt['scope']['group_ids'] == [11, 33]
            assert {m['group_id'] for m in prompt['messages']} == {11, 33}
            return {'answer': '配置已确认', 'source_ids': ['s1', 's999']}
    result = service.ask(data['ticket'], 'owner', AI())
    assert result['scope']['group_ids'] == [11, 33]
    source = service.source(data['ticket'], 'owner', 's1')
    assert source['group_id'] in (11, 33) and source['scope']['group_ids'] == [11, 33]
    with pytest.raises(LookupError):
        service.source(data['ticket'], 'owner', 's999')
    with pytest.raises(LookupError):
        service.preview(owner='owner', question='配置方案？', group_ids=[11, 999],
                        date_from='2026-10-06', date_to='2026-10-07')


@pytest.mark.parametrize('scope', [{'group_ids': []}, {'group_id': 11, 'group_ids': [33]}])
def test_all_query_services_reject_ambiguous_or_empty_selection(seeded, scope):
    for query in (
        lambda: search_archive(seeded, query='配置', **scope),
        lambda: CandidateService(seeded).query(**scope),
        lambda: KnowledgeLibrary(seeded, None).list_items(**scope),
        lambda: ArchiveQAService(seeded).preview(owner='owner', question='配置方案？',
            date_from='2026-10-06', date_to='2026-10-07', **scope),
    ):
        with pytest.raises(ValueError):
            query()


def test_omitted_and_legacy_single_group_selection_remain_compatible(seeded):
    assert search_archive(seeded, query='配置')['total'] == 75
    assert search_archive(seeded, query='配置', group_id=22)['total'] == 25
    assert len(CandidateService(seeded).query(status='all', group_id=22)) == 12
    assert KnowledgeLibrary(seeded, None).list_items(group_id=22)['total'] == 12


@pytest.fixture
def route_client(seeded):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qq_digest.operations import OperationCoordinator
    from qq_digest.web.search_routes import add_search_routes
    from qq_digest.web.knowledge_routes import add_knowledge_routes
    from qq_digest.web.archive_qa_routes import add_archive_qa_routes
    app = FastAPI()
    args = dict(archive=seeded, config=None, require_login=lambda: None)
    ops = OperationCoordinator()
    add_search_routes(app, **args)
    add_knowledge_routes(app, **args, knowledge=None, templates=None, operations=ops)
    add_archive_qa_routes(app, **args, templates=None, operations=ops)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(('url', 'key', 'total'), [
    ('/api/search', 'results', 50), ('/api/knowledge', 'items', 24),
])
def test_routes_use_repeated_group_ids_and_reject_conflicts(route_client, url, key, total):
    response = route_client.get(url, params=[('q', '配置'), ('group_ids', 11), ('group_ids', 33)])
    assert response.status_code == 200
    assert response.json()['total'] == total
    assert response.json()[key]
    response = route_client.get(url, params=[('q', '配置'), ('group_id', 22), ('group_ids', 11)])
    assert response.status_code == 422
    assert route_client.get(url, params={'group_ids': ''}).status_code == 422
    assert route_client.get(url, params={'group_ids': str(2**63)}).status_code == 422


def test_qa_route_validates_json_scope_before_preview(route_client):
    payload = dict(question='配置方案？', query='配置', group_ids=[11, 33],
                   date_from='2026-10-06', date_to='2026-10-07')
    response = route_client.post('/api/archive-qa/preview', json=payload)
    assert response.status_code == 200
    assert response.json()['scope']['group_ids'] == [11, 33]
    assert response.json()['matched_count'] == 24
    for override in ({'group_ids': []}, {'group_ids': [True]}, {'group_id': False},
                     {'group_id': 22}, {'group_ids': [2**63]}):
        response = route_client.post('/api/archive-qa/preview', json={**payload, **override})
        assert response.status_code == 422
