import re

import pytest

from tests.test_web import web_client


def login(client):
    client.post('/login', data={'password': 'password123'})


@pytest.mark.parametrize('path,active', [('/', 'reports'),('/reports','reports'),('/search','search'),('/groups','groups'),('/collect','groups'),('/settings','settings'),('/ai-settings','settings'),('/overview','settings')])
def test_four_primary_entries_and_section_membership(web_client, path, active):
    client, _, _ = web_client
    login(client)
    response = client.get(path)
    assert response.status_code == 200
    nav = re.search(r'<nav[^>]+aria-label="主要导航"[^>]*>(.*?)</nav>', response.text, re.S).group(1)
    assert re.findall(r'>(摘要|查找|群聊|设置)</a>',nav) == ['摘要','查找','群聊','设置']
    assert len(re.findall(r'<a\s',nav)) == 4
    assert re.search(r'id="nav-'+active+r'"[^>]*aria-current="page"', nav)


def test_default_home_is_reading_and_overview_remains_available(web_client):
    client, _, _ = web_client
    login(client)
    home = client.get('/').text
    assert 'id="summary-points"' in home
    assert 'id="job-list"' not in home
    assert 'id="job-list"' in client.get('/overview').text
    client.cookies.clear()
    assert client.get('/overview',follow_redirects=False).status_code == 303


def test_optional_tools_are_visible_in_header_and_follow_feature_switches(web_client):
    client, _, _ = web_client
    login(client)
    page = client.get('/').text
    header = page.split('</header>', 1)[0]
    tools = re.search(r'<nav[^>]+id="workspace-tools-area"[^>]*>(.*?)</nav>', header, re.S)
    assert tools is not None, 'tool links must be visible directly in the shared header'
    assert '<details' not in tools.group(1)
    for path in ('/knowledge', '/tasks', '/candidates', '/failures', '/settings?section=features'):
        assert 'href="'+path+'"' in tools.group(1)
    assert 'href="/topics"' not in tools.group(1) and 'href="/reminders"' not in tools.group(1)
    state = client.get('/api/features').json()
    client.put('/api/features',json={'values':{'topics':True,'reminders':True},'expected_revision':state['revision']})
    page = client.get('/topics').text
    tools = re.search(r'<nav[^>]+id="workspace-tools-area"[^>]*>(.*?)</nav>', page, re.S).group(1)
    assert re.search(r'href="/topics"[^>]*aria-current="page"', tools)
    assert 'href="/reminders"' in tools
    state = client.get('/api/features').json()
    client.put('/api/features',json={'values':{'tasks':False,'review':False,'failures':False,'topics':False,'reminders':False},'expected_revision':state['revision']})
    page = client.get('/').text
    assert 'href="/tasks"' not in page and 'href="/candidates"' not in page
    assert 'href="/failures"' not in page
    assert 'href="/topics"' not in page and 'href="/reminders"' not in page
    assert 'href="/knowledge"' in page and 'href="/settings?section=features"' in page


def test_settings_categories_and_group_details(web_client):
    client, _, _ = web_client
    login(client)
    page = client.get('/settings').text
    for label in ('AI 连接','自动运行','界面与功能','存储与导出','备份与恢复','运行记录'):
        assert label in page
    assert 'id="automatic-form"' in page and 'id="features-form"' in page
    assert 'id="deepseek-key-form"' in page
    groups = client.get('/groups').text
    assert 'id="group-detail"' in groups
    assert '添加群聊' in groups and '高级设置' in groups
