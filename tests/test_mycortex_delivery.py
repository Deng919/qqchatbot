from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig, NormalizedMessage
from tests.test_web import web_client


NOW = datetime(2026, 11, 19, 8, tzinfo=timezone.utc)


@pytest.fixture
def source(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    archive.seed_groups([GroupConfig(group_id=123, name='测试群'), GroupConfig(group_id=456, name='其他群')])
    archive.ingest([
        NormalizedMessage(group_id=group, msg_id=mid, text=text, timestamp=stamp, collected_at=NOW)
        for group, mid, text, stamp in [
            (123, 'one', '复习时先重做课堂例题。', NOW),
            (123, 'two', '先用真题确认薄弱章节。', NOW + timedelta(minutes=1)),
            (456, 'other', '不应传出的其他群消息', NOW),
            (123, 'old', '旧资料不自动提交', NOW - timedelta(days=10)),
        ]
    ])
    yield archive
    archive.close()


def deliver(source, tmp_path, **kwargs):
    from qq_digest.mycortex import MyCortexDelivery

    config = SimpleNamespace(mycortex=SimpleNamespace(store_dir=tmp_path, connection_id='qq-digest'),
                             summary=SimpleNamespace(timezone='Asia/Shanghai'))
    return MyCortexDelivery(source, config, **kwargs)


def organization(quote='复习时先重做课堂例题。', mid='one'):
    return {'topics': ['学习'], 'entries': [{'messageId': mid, 'summary': '复习先重做课堂例题',
            'subject': '群友', 'stance': 'other-view', 'quote': quote, 'timeText': None}]}


def test_refinement_and_receipt_are_shared_by_manual_retries(source, tmp_path):
    payloads = []

    def transport(config, payload):
        payloads.append(payload)
        return {'status': 'saved', 'requestId': payload['requestId'],
                'id': 'd5a78094-7380-4b73-8528-43ce904d9043', 'revision': 1}

    service = deliver(source, tmp_path, refine=lambda config, rows: organization(), transport=transport)
    first = service.submit(123, ['two', 'one'])
    second = service.submit(123, ['one', 'two', 'one'])
    assert first == second
    assert first['status'] == 'saved'
    assert len(payloads) == 1
    assert payloads[0]['sourceUri'] == 'qqdigest://group/123/message/one'
    entry = payloads[0]['organization']['entries'][0]
    assert 'messageId' not in entry
    assert entry['quote'] == '复习时先重做课堂例题。'
    assert payloads[0]['body'] == entry['quote']


def test_missing_cross_group_and_unanchored_quotes_never_submit(source, tmp_path):
    def no_transport(*args):
        pytest.fail('invalid or empty preparation reached MyCortex')

    service = deliver(source, tmp_path, refine=lambda config, rows: organization(mid='two'), transport=no_transport)
    with pytest.raises(LookupError):
        service.submit(123, ['one', 'other'])
    with pytest.raises(ValueError, match='引文'):
        service.submit(123, ['one', 'two'])
    service = deliver(source, tmp_path, refine=lambda config, rows: {'topics': [], 'entries': []}, transport=no_transport)
    assert service.submit(123, ['one'])['status'] == 'skipped'


def test_lost_receipt_reuses_prepared_payload_and_retains_cited_sources(source, tmp_path):
    from qq_digest.mycortex_transport import MyCortexUnavailable

    payloads = []

    def transport(config, payload):
        payloads.append(payload)
        if len(payloads) == 1:
            raise MyCortexUnavailable('回执丢失')
        return {'status': 'saved', 'requestId': payload['requestId'],
                'id': 'd5a78094-7380-4b73-8528-43ce904d9043', 'revision': 1}

    result = organization('先用真题确认薄弱章节。', 'two')
    result['entries'].extend(organization()['entries'])
    service = deliver(source, tmp_path, refine=lambda config, rows: result, transport=transport)
    with pytest.raises(MyCortexUnavailable):
        service.submit(123, ['one', 'two'])

    def no_refinement(*args):
        pytest.fail('retry should reuse the prepared refinement')

    restarted = deliver(source, tmp_path, refine=no_refinement, transport=transport)
    assert restarted.submit(123, ['two', 'one'])['status'] == 'saved'
    assert payloads[0] == payloads[1]
    assert payloads[0]['sourceUri'] == 'qqdigest://group/123/message/two?message_id=one'
    assert payloads[0]['observedAt'] == (NOW + timedelta(minutes=1)).isoformat()

    alternate = deliver(source, tmp_path / 'another-library', refine=lambda config, rows: result,
                        transport=transport)
    assert alternate.submit(123, ['one', 'two'])['status'] == 'saved'
    assert len(payloads) == 3
    source.delete_group(123)
    assert source.connection.execute('SELECT COUNT(*) FROM mycortex_receipts').fetchone()[0] == 0


def test_automatic_only_processes_new_published_candidates(source, tmp_path):
    service = deliver(source, tmp_path, refine=lambda config, rows: organization(), transport=lambda cfg, p: {
        'status': 'saved', 'requestId': p['requestId'], 'id': 'd5a78094-7380-4b73-8528-43ce904d9043', 'revision': 1,
    })
    since = NOW - timedelta(minutes=1)
    assert service.process_automatic(now=NOW) is None
    service.set_automatic(123, True, now=since)
    candidates = CandidateService(source)
    ids = [candidates.create(group_id=123, message_ids=[mid], created_date='2026-11-19',
                             candidate_type='resource', title=mid, reason='有参考价值')
           for mid in ['old', 'one', 'two']]
    source.connection.execute('UPDATE candidates SET created_at=?', (NOW.isoformat(),))
    source.connection.execute('INSERT INTO reports (group_id,report_date,markdown_path,json_path,candidate_ids,created_at) VALUES (?,?,?,?,?,?)',
                              (123, '2026-11-19', 'fixture.md', 'fixture.json', f'[{ids[0]},{ids[1]}]', NOW.isoformat()))
    source.connection.commit()
    assert service.process_automatic(now=NOW)['status'] == 'saved'
    assert service.process_automatic(now=NOW) is None
    assert service.automatic_state(123)['last_status'] == 'saved'


def test_automatic_retries_with_backoff_and_stops_on_disable(source, tmp_path):
    from qq_digest.mycortex_transport import MyCortexUnavailable

    payloads = []

    def unavailable(config, payload):
        payloads.append(payload)
        raise MyCortexUnavailable('暂不可用')

    service = deliver(source, tmp_path, refine=lambda config, rows: organization(), transport=unavailable)
    service.set_automatic(123, True, now=NOW - timedelta(minutes=1))
    candidate_id = CandidateService(source).create(group_id=123, message_ids=['one'], created_date='2026-11-19',
                                                   candidate_type='resource', title='复习', reason='值得留存')
    source.connection.execute('UPDATE candidates SET created_at=? WHERE candidate_id=?', (NOW.isoformat(), candidate_id))
    source.connection.execute('INSERT INTO reports (group_id,report_date,markdown_path,json_path,candidate_ids,created_at) VALUES (?,?,?,?,?,?)',
                              (123, '2026-11-19', 'fixture.md', 'fixture.json', f'[{candidate_id}]', NOW.isoformat()))
    source.connection.commit()
    assert service.process_automatic(now=NOW)['status'] == 'retry'
    assert service.process_automatic(now=NOW + timedelta(minutes=4)) is None
    assert service.process_automatic(now=NOW + timedelta(minutes=5))['status'] == 'retry'
    assert service.process_automatic(now=NOW + timedelta(minutes=15))['status'] == 'failed'
    assert service.automatic_state(123)['last_status'] == 'failed'
    service.set_automatic(123, False)
    assert service.process_automatic(now=NOW + timedelta(hours=1)) is None
    assert len(payloads) == 3 and payloads[0] == payloads[1] == payloads[2]


def test_versioned_submission_and_group_settings_require_login(web_client):
    client, _, _ = web_client
    payload = {'group_id': 123, 'message_ids': ['m1']}
    assert client.post('/api/mycortex/v1/submissions', json=payload).status_code == 401
    assert client.get('/api/mycortex/v1/groups/123/auto').status_code == 401
    client.post('/login', data={'password': 'password123'})
    assert client.post('/api/mycortex/v1/submissions', json=payload).status_code == 503
    assert client.post('/api/mycortex/v1/submissions', json={**payload, 'body': '外部原文'}).status_code == 422
