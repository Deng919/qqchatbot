import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_message_browsing import browsing_client, add_message, login
from qq_digest.models import NormalizedMessage


def test_search_messages_preserve_precise_time_and_sender(browsing_client):
    client, archive, _ = browsing_client
    stamp = datetime(2026, 10, 4, 3, 12, tzinfo=timezone.utc)
    add_message(archive, 'find-1', stamp, text='接口配置已恢复', sender=1001)
    login(client)
    item = client.get('/api/search?q=配置&kind=message').json()['results'][0]
    assert item['timestamp'] == stamp.isoformat()
    assert item['sender_qq'] == 1001


def test_find_page_has_search_entry_and_separate_context(browsing_client):
    client, _, _ = browsing_client
    login(client)
    page = client.get('/search').text
    assert '<h1>查找</h1>' in page
    assert 'id="find-home"' in page and '最近搜索' in page
    assert 'id="find-context"' in page and 'role="dialog"' in page
    assert '查看消息／搜索' not in page
    assert 'id="search-sender"' in page
    assert 'id="search-message-type"' in page


def test_search_api_message_filters_auth_and_validation(browsing_client):
    client, archive, _ = browsing_client
    params = dict(q='配置', kind='message', sender_qq=1001, message_type='file', group_id=11,
                  date_from='2026-10-06', date_to='2026-10-06')
    assert client.get('/api/search', params=params).status_code == 401
    assert client.get('/api/search?sender_qq=invalid').status_code == 401
    stamp = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    archive.ingest([NormalizedMessage(msg_id='file-hit', group_id=11, sender_qq=1001,
        message_type='file', text='文件配置', timestamp=stamp, collected_at=stamp)])
    add_message(archive, 'text-miss', stamp, text='文件配置', sender=1001)
    login(client)
    response = client.get('/api/search', params=params)
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['results']] == ['file-hit']
    for patch in (dict(sender_qq=0), dict(sender_qq=2**63), dict(message_type='image'),
                  dict(kind='all'), dict(kind='knowledge')):
        assert client.get('/api/search', params={**params, **patch}).status_code == 422


@pytest.mark.parametrize('script', ['find_helpers.cjs', 'find_interactions.cjs', 'message_context_ui.cjs', 'knowledge_ui.cjs', 'bookmarks_ui.cjs', 'summary_clean_display.cjs', 'archive_qa_ui.cjs'])
def test_find_helpers_behavior(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root/'tests'/script)], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
