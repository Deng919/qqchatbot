from datetime import datetime, timezone

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.search import search_archive


def test_same_date_knowledge_pages_have_no_duplicates_or_missing_items(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    archive.upsert_groups([GroupConfig(group_id=1, name='测试群')])
    service = CandidateService(archive)
    ids = []
    for number in range(25):
        cid = service.create(group_id=1, created_date='2026-10-05',
                             candidate_type='experience', title=f'分页知识 {number}',
                             reason='检查翻页', message_ids=[f'message-{number}'])
        service.update_status(cid, 'confirmed')
        archive.connection.execute('INSERT INTO knowledge_items VALUES(?,?,?,?)',
                                   (str(cid), cid, 'unused.md', '2026-10-05'))
        ids.append(cid)
    archive.connection.commit()
    pages = [search_archive(archive, query='分页', kind='knowledge', page=p)['results']
             for p in (1, 2)]
    found = [item['id'] for page in pages for item in page]
    assert found == sorted(ids, reverse=True)
    assert len(set(found)) == 25
    archive.close()


def test_equal_time_report_and_message_pages_use_stable_tiebreaks(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    stamp = datetime(2026, 10, 5, tzinfo=timezone.utc)
    for gid in range(1, 26):
        archive.upsert_groups([GroupConfig(group_id=gid, name=f'群 {gid}')])
        archive.ingest([NormalizedMessage(msg_id='same-id', group_id=gid,
            timestamp=stamp, collected_at=stamp, text='分页消息')])
        markdown = tmp_path / f'{gid}.md'
        markdown.write_text('分页报告', encoding='utf-8')
        archive.record_report(group_id=gid, report_date='2026-10-05',
            markdown_path=markdown, json_path=tmp_path / f'{gid}.json', candidate_ids=[])
    archive.connection.execute('UPDATE reports SET created_at=?', (stamp.isoformat(),))
    archive.connection.commit()
    pages = [search_archive(archive, query='分页', page=p)['results'] for p in (1, 2, 3)]
    identities = [(item['kind'], item['group_id'], item['id']) for page in pages for item in page]
    assert len(identities) == len(set(identities)) == 50
    for kind in ('message', 'report'):
        assert {group for item_kind, group, _ in identities if item_kind == kind} == set(range(1, 26))
    archive.close()
