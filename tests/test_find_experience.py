import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_message_browsing import browsing_client, add_message, login


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


@pytest.mark.parametrize('script', ['find_helpers.cjs', 'find_interactions.cjs', 'message_context_ui.cjs'])
def test_find_helpers_behavior(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root/'tests'/script)], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
