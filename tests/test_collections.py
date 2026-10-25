from urllib.parse import parse_qs, urlsplit
import re

from tests.test_message_browsing import browsing_client
from tests.test_knowledge_library import setup_source, save_payload


def test_collections_has_one_navigation_entry_and_two_views(browsing_client):
    client, archive, _ = browsing_client
    setup_source(client, archive)
    saved = client.get('/bookmarks')
    header = saved.text.split('</header>', 1)[0]
    assert 'href="/bookmarks"' in header
    assert 'href="/knowledge"' not in header
    assert 'href="/tasks"' not in header and 'href="/topics"' not in header
    assert '已收藏' in saved.text and '整理的资料' in saved.text
    assert 'id="bookmarks-list"' in saved.text
    knowledge = client.get('/bookmarks?view=knowledge')
    assert knowledge.status_code == 200
    assert 'id="knowledge-list"' in knowledge.text
    assert '<h1>收藏</h1>' in knowledge.text


def test_old_knowledge_links_preserve_filters_and_item(browsing_client):
    client, archive, _ = browsing_client
    setup_source(client, archive)
    response = client.get('/knowledge?item_id=3&q=%E9%85%8D%E7%BD%AE&group_ids=11&group_ids=12&page=2', follow_redirects=False)
    assert response.status_code == 303
    target = urlsplit(response.headers['location'])
    assert target.path == '/bookmarks'
    assert parse_qs(target.query) == {'view':['knowledge'],'item_id':['3'],'q':['配置'],'group_ids':['11','12'],'page':['2']}


def test_collection_sources_and_knowledge_data_are_preserved(browsing_client):
    client, archive, config = browsing_client
    setup_source(client, archive)
    bookmark = client.post('/api/bookmarks', json={'kind':'message','group_id':11,'msg_id':'knowledge-source'}).json()
    item = client.post('/api/knowledge/from-message', json=save_payload()).json()
    assert client.get('/api/bookmarks?status=all').json()['total'] == 1
    assert client.get('/api/knowledge').json()['total'] == 1
    assert client.get('/api/bookmarks/'+str(bookmark['bookmark_id'])).json()['origin_available']
    assert client.get('/bookmarks?view=knowledge&item_id='+str(item['item_id'])).status_code == 200
    assert '先核对地址' in (config.knowledge_dir/'experiences.md').read_text('utf-8')
    assert client.get('/api/knowledge/'+str(item['item_id'])).json()['message_ids'] == ['knowledge-source']


def test_collection_materials_accessible_when_snapshot_bookmarks_disabled(browsing_client):
    client, archive, _ = browsing_client
    setup_source(client, archive)
    features = client.app.state.features
    features.update({'bookmarks':False}, features.snapshot()['revision'])
    page = client.get('/bookmarks')
    assert page.status_code == 200
    assert 'id="knowledge-list"' in page.text
    assert 'id="bookmarks-list"' not in page.text
    assert 'href="/bookmarks"' in page.text.split('</header>', 1)[0]
    assert client.get('/api/bookmarks').status_code == 403


def test_collection_review_is_a_local_tab(browsing_client):
    client, archive, _ = browsing_client
    setup_source(client, archive)
    features = client.app.state.features
    features.update({'review':True}, features.snapshot()['revision'])
    page = client.get('/bookmarks?view=knowledge').text
    assert re.search(r'<nav[^>]+aria-label="收藏分类"', page)
    assert 'href="/candidates"' in page
    review = client.get('/candidates').text
    assert re.search(r'href="/bookmarks"[^>]*aria-current="page"', review.split('</header>',1)[0])
