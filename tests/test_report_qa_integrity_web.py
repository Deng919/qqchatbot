from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qq_digest.models import NormalizedMessage
from qq_digest.report_revisions import ReportRevisionService
from tests.test_message_browsing import browsing_client, login


def publish_revision(client, archive, config, tmp_path, payload):
    login(client)
    features = client.get('/api/features').json()
    assert client.put('/api/features', json={'values': {'report_qa': True},
        'expected_revision': features['revision']}).status_code == 200
    path = tmp_path / 'report.md'
    path.write_text('可变文件旧正文', encoding='utf-8')
    report_id = archive.record_report(group_id=11, report_date='2026-09-02',
        markdown_path=path, json_path=tmp_path / 'missing.json', candidate_ids=[])
    with archive.transaction():
        ReportRevisionService(archive).record_generated_in_transaction('daily', report_id,
            '当前不可变版本正文', payload, '合成验证')
    zone = ZoneInfo(config.summary.timezone)
    for msg_id, hour, minute in [('before', 9, 29), ('at-start', 9, 30), ('inside', 10, 0),
                                 ('cutoff', 11, 0), ('after', 11, 1)]:
        stamp = datetime(2026, 9, 2, hour, minute, tzinfo=zone)
        archive.ingest([NormalizedMessage(msg_id=msg_id, group_id=11, timestamp=stamp,
            collected_at=stamp, text=msg_id)])
    return report_id, path


def test_report_ask_endpoint_uses_snapshot_window_and_body(browsing_client, tmp_path, monkeypatch):
    client, archive, config = browsing_client
    payload = {'window_start': '2026-09-02T09:30:00+14:00',
        'window_end': '2026-09-02T11:00:00+14:00', 'window_end_inclusive': False}
    report_id, path = publish_revision(client, archive, config, tmp_path, payload)
    monkeypatch.setenv(config.ai.api_key_env, 'synthetic')
    prompts = []
    class FakeAI:
        def __init__(self, **kwargs):
            # Evidence is already loaded; a later file edit must not replace its body.
            path.write_text('回答前被覆盖的正文', encoding='utf-8')
        def chat(self, messages):
            prompts.extend(messages)
            return {'answer': '范围内消息', 'source_ids': ['inside', 'before', 'cutoff', 'after']}
        def close(self):
            pass
    monkeypatch.setattr('qq_digest.web.app.AIClient', FakeAI)

    response = client.post(f'/api/reports/daily/{report_id}/ask', json={'question': '消息范围？'})

    assert response.status_code == 200
    assert response.json()['context_message_count'] == 2
    assert [row['msg_id'] for row in response.json()['sources']] == ['inside']
    assert '当前不可变版本正文' in prompts[1]['content']
    assert '被覆盖' not in prompts[1]['content'] and '旧正文' not in prompts[1]['content']
    assert '09:30:00+14:00' in prompts[1]['content']


@pytest.mark.parametrize('window', [{'window_start': 'broken'}, {
    'window_start': '2026-09-02T09:30:00', 'window_end': '2026-09-02T11:00:00'}])
def test_report_ask_corrupt_window_is_422_without_ai(browsing_client, tmp_path, monkeypatch, window):
    client, archive, config = browsing_client
    report_id, _ = publish_revision(client, archive, config, tmp_path, window)
    def forbidden(*args, **kwargs):
        raise AssertionError('Corrupt windows must be rejected before constructing AI')
    monkeypatch.setattr('qq_digest.web.app.AIClient', forbidden)

    response = client.post(f'/api/reports/daily/{report_id}/ask', json={'question': '消息范围？'})

    assert response.status_code == 422
    assert '时间窗' in response.json()['detail']
