import json
import shutil
from datetime import datetime, timezone

import pytest

from qq_digest.archive import Archive
from tests.test_message_browsing import browsing_client, add_message, login


def setup(client, archive):
    login(client)
    add_message(archive, 'bookmark-message', datetime(2026, 10, 4, tzinfo=timezone.utc),
                text='完整原文\n' + '排查步骤 ' * 120)


def save(client, **payload):
    return client.post('/api/bookmarks', json=payload)


def report(archive, config, kind='daily'):
    config.report_dir.mkdir(exist_ok=True)
    path = config.report_dir / (kind + '.json')
    path.write_text(json.dumps({'evidence_version': 1, 'main_topics': [
        {'topic': '连接排查', 'summary': '核对地址再重启', 'message_ids': ['bookmark-message']}]}), encoding='utf-8')
    md = path.with_suffix('.md'); md.write_text('连接排查', encoding='utf-8')
    if kind == 'daily':
        rid = archive.record_report(group_id=11, report_date='2026-10-04',
            markdown_path=str(md), json_path=str(path), candidate_ids=[])
    else:
        rid = archive.record_manual_report(group_id=11, start_date='2026-10-03', end_date='2026-10-04',
            detail_mode='concise', markdown_path=str(md), json_path=str(path), candidate_ids=[],
            effective_template='concise', input_fingerprint='test', source_message_count=1)
    return rid, path


def test_bookmarks_auth_and_independent_switch(browsing_client):
    client, archive, _ = browsing_client
    assert client.get('/api/bookmarks').status_code == 401
    assert save(client, kind='message', group_id=11, msg_id='x').status_code == 401
    assert client.get('/bookmarks', follow_redirects=False).headers['location'] == '/login'
    setup(client, archive)
    assert 'href="/bookmarks"' in client.get('/').text.split('</header>')[0]
    state = client.get('/api/features').json()
    assert client.put('/api/features', json={'values': {'bookmarks': False}, 'expected_revision': state['revision']}).status_code == 200
    assert client.get('/api/bookmarks').status_code == 403
    assert save(client, kind='message', group_id=11, msg_id='bookmark-message').status_code == 403
    assert client.get('/bookmarks', follow_redirects=False).headers['location'] == '/settings?feature=bookmarks'
    assert 'href="/bookmarks"' not in client.get('/').text.split('</header>')[0]


def test_message_snapshot_dedup_state_conflict_and_reopen(browsing_client):
    client, archive, config = browsing_client
    setup(client, archive)
    payload = dict(kind='message', group_id=11, msg_id='bookmark-message')
    response = save(client, **payload); assert response.status_code == 200, response.text
    one = response.json()
    assert one['status'] == 'pending' and one['snapshot']['text'].endswith('排查步骤 ' * 120)
    bid = one['bookmark_id']
    complete = client.patch(f'/api/bookmarks/{bid}', json={'status': 'completed', 'expected_revision': 0})
    assert complete.status_code == 200
    assert save(client, **payload).json()['status'] == 'completed'
    assert client.get('/api/bookmarks').json()['total'] == 0
    assert client.get('/api/bookmarks?status=completed').json()['total'] == 1
    assert client.patch(f'/api/bookmarks/{bid}', json={'status': 'pending', 'expected_revision': 0}).status_code == 409
    assert client.delete(f'/api/bookmarks/{bid}?expected_revision=0').status_code == 409
    assert client.patch(f'/api/bookmarks/{bid}', json={'status': 'pending', 'expected_revision': 1}).status_code == 200
    copy = config.data_dir/'reopened.sqlite'; archive.connection.commit(); shutil.copy2(config.archive_path, copy)
    reopened = Archive.open(copy)
    assert reopened.connection.execute('SELECT status FROM bookmarks').fetchone()['status'] == 'pending'
    reopened.close()
    assert client.delete(f'/api/bookmarks/{bid}?expected_revision=2').status_code == 200
    assert client.get('/api/bookmarks?status=all').json()['total'] == 0


def test_invalid_sources_and_payload_cannot_inject_content(browsing_client):
    client, archive, _ = browsing_client; setup(client, archive)
    assert save(client, kind='message', group_id=22, msg_id='bookmark-message').status_code == 404
    for payload in (dict(kind='message', group_id=11, msg_id='bookmark-message', text='伪造内容'),
                    dict(kind='message', group_id=True, msg_id='bookmark-message'),
                    dict(kind='knowledge', candidate_id=0), dict(kind='report', report_id=1),
                    dict(kind='message', group_id=11, msg_id='bookmark-message', candidate_id=1)):
        assert save(client, **payload).status_code == 422
    assert save(client, kind='knowledge', candidate_id=1).status_code == 404
    assert client.get('/api/bookmarks').json()['total'] == 0


@pytest.mark.parametrize('kind', ['daily', 'range'])
def test_report_paragraph_is_verified_and_survives_rewrite(browsing_client, kind):
    client, archive, config = browsing_client; setup(client, archive)
    rid, path = report(archive, config, kind)
    point = client.get('/api/summary-reading?date_from=2026-10-03&date_to=2026-10-04').json()['items'][0]
    payload = dict(kind='report', report_kind=kind, report_id=rid, point_key=point['key'])
    response = save(client, **payload); assert response.status_code == 200, response.text
    item = response.json()
    assert item['snapshot']['text'] == '连接排查：核对地址再重启'
    detail = client.get(f"/api/bookmarks/{item['bookmark_id']}").json()
    assert detail['origin_available'] and detail['origin_url'] == f'/reports?kind={kind}&id={rid}'
    path.write_text('{"evidence_version":1,"main_topics":[{"topic":"改变后的话题"}]}', encoding='utf-8')
    assert save(client, **dict(payload, point_key='0'*64)).status_code == 404
    detail = client.get(f"/api/bookmarks/{item['bookmark_id']}").json()
    assert not detail['origin_available'] and detail['snapshot']['text'] == item['snapshot']['text']
    path.unlink()
    assert client.get(f"/api/bookmarks/{item['bookmark_id']}").status_code == 200


def test_resource_and_knowledge_sources_ignore_review_switch(browsing_client):
    client, archive, _ = browsing_client; setup(client, archive)
    cid = client.app.state.candidates.create(group_id=11, created_date='2026-10-04',
        candidate_type='resource', title='参考文档', link='https://example.com/doc',
        content='资源正文', reason='排查', message_ids=['bookmark-message'])
    resource = save(client, kind='resource', candidate_id=cid)
    assert resource.status_code == 200, resource.text
    assert save(client, kind='knowledge', candidate_id=cid).status_code == 404
    client.app.state.knowledge_library._publish(cid); archive.connection.commit()
    knowledge = save(client, kind='knowledge', candidate_id=cid)
    assert knowledge.status_code == 200 and knowledge.json()['bookmark_id'] != resource.json()['bookmark_id']
    state = client.app.state.features.snapshot()
    client.app.state.features.update({'review': False}, state['revision'])
    kid = knowledge.json()['bookmark_id']; rid = resource.json()['bookmark_id']
    assert client.get(f'/api/bookmarks/{kid}').json()['origin_url'] == f'/knowledge?item_id={cid}'
    assert client.get(f'/api/bookmarks/{rid}').json()['origin_url'] == 'https://example.com/doc'


def test_message_missing_keeps_snapshot_but_group_deletion_removes_it(browsing_client):
    client, archive, _ = browsing_client; setup(client, archive)
    item = save(client, kind='message', group_id=11, msg_id='bookmark-message').json(); bid = item['bookmark_id']
    assert client.get(f'/api/bookmarks/{bid}/context').json()['anchor_id'] == 'bookmark-message'
    archive.connection.execute("DELETE FROM messages WHERE group_id=11"); archive.connection.commit()
    detail = client.get(f'/api/bookmarks/{bid}').json()
    assert not detail['origin_available'] and detail['snapshot']['text'].startswith('完整原文')
    assert client.get(f'/api/bookmarks/{bid}/context').status_code == 404
    archive.delete_group(11)
    assert client.get(f'/api/bookmarks/{bid}').status_code == 404


def test_filters_pagination_literal_search_and_operation_mutex(browsing_client):
    client, archive, _ = browsing_client; setup(client, archive)
    for index in range(23):
        mid = f'p{index}'
        add_message(archive, mid, datetime(2026, 10, 4, tzinfo=timezone.utc), text=f'文字_{index}%')
        assert save(client, kind='message', group_id=11, msg_id=mid).status_code == 200
    data = client.get('/api/bookmarks?page=2').json()
    assert data['total'] == 23 and len(data['items']) == 3
    assert client.get('/api/bookmarks?q=文字_1%').json()['total'] == 1
    assert client.get('/api/bookmarks?kind=knowledge').json()['total'] == 0
    for query in ('page=0', 'status=bad', 'kind=bad'):
        assert client.get('/api/bookmarks?' + query).status_code == 422
    with client.app.state.operations.claim('backup'):
        assert save(client, kind='message', group_id=11, msg_id='bookmark-message').status_code == 409


def test_saved_content_remains_readable_during_background_work(browsing_client):
    client, archive, _ = browsing_client; setup(client, archive)
    item = save(client, kind='message', group_id=11, msg_id='bookmark-message').json()
    bid = item['bookmark_id']
    with client.app.state.operations.claim('daily'):
        for url in ('/api/bookmarks', f'/api/bookmarks/{bid}', f'/api/bookmarks/{bid}/context'):
            assert client.get(url).status_code == 200
        assert client.patch(f'/api/bookmarks/{bid}', json={'status': 'completed', 'expected_revision': 0}).status_code == 409
    assert client.patch(f'/api/bookmarks/{bid}', json={'status': 'completed', 'expected_revision': 2**63}).status_code == 422
    assert client.delete(f'/api/bookmarks/{bid}?expected_revision={2**63}').status_code == 422


def test_bookmarks_survive_actual_backup_restore(tmp_path):
    from tests.test_backup_restore import _data
    from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
    from qq_digest.bookmarks import BookmarkService
    source, old_config = _data(tmp_path, 'source')
    _, current_config = _data(tmp_path, 'current')
    archive = Archive.open(source/'archive/archive.sqlite')
    saved = BookmarkService(archive).save({'kind': 'message', 'group_id': 123, 'msg_id': 'm1'})
    BookmarkService(archive).update(saved['bookmark_id'], 'completed', 0); archive.close()
    backup = backup_data(old_config, tmp_path/'backups', temp_root=tmp_path/'cache')
    preview = inspect_backup(backup['path'], temp_root=tmp_path/'cache')
    install = tmp_path/'install'; install.mkdir()
    (install/'launcher.json').write_text(json.dumps({'config_path': str(current_config)}), encoding='utf-8')
    destination = tmp_path/'restored'
    restore_backup(backup['path'], destination, current_config, install, backup_root=tmp_path/'backups',
                   temp_root=tmp_path/'cache', expected_sha256=preview['sha256'])
    restored = Archive.open(destination/'archive/archive.sqlite')
    detail = BookmarkService(restored).detail(saved['bookmark_id'])
    assert detail['status'] == 'completed' and detail['revision'] == 1
    assert detail['snapshot']['text'] == 'hello' and detail['origin_available']
    restored.close()


@pytest.mark.parametrize('link', ['javascript:alert(1)', 'data:text/html,bad', '//example.com', 'https://'])
def test_resource_bookmarks_do_not_publish_unsafe_links(browsing_client, link):
    client, archive, _ = browsing_client; setup(client, archive)
    cid = client.app.state.candidates.create(group_id=11, created_date='2026-10-04',
        candidate_type='resource', title='资源', link=link, content='保存内容', reason='', message_ids=['bookmark-message'])
    response = save(client, kind='resource', candidate_id=cid); assert response.status_code == 200
    detail = client.get(f"/api/bookmarks/{response.json()['bookmark_id']}").json()
    assert detail['origin_available'] and detail['origin_url'] == ''
    assert detail['snapshot']['link'] == ''
