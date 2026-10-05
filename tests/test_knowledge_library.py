from datetime import datetime, timezone

import pytest

from tests.test_message_browsing import browsing_client, add_message, login


def save_payload(**overrides):
    return dict(group_id=11, msg_id='knowledge-source', item_type='experience',
                title='配置恢复步骤', reason='下次排查时参考', link='', **overrides)


def setup_source(client, archive, text='先核对地址。\n再重启接口。'):
    add_message(archive, 'knowledge-source', datetime(2026, 10, 4, 15, tzinfo=timezone.utc), text=text)
    login(client)
    service = client.app.state.features
    service.update({'review': False}, service.snapshot()['revision'])


def test_knowledge_access_requires_login(browsing_client):
    client, _, _ = browsing_client
    for url in ('/api/knowledge', '/api/knowledge/1',
                '/api/knowledge/from-message?group_id=11&msg_id=x', '/api/knowledge/1/sources/x'):
        assert client.get(url).status_code == 401
    assert client.post('/api/knowledge/from-message', json=save_payload()).status_code == 401
    assert client.get('/knowledge', follow_redirects=False).headers['location'] == '/login'


def test_save_and_browse_with_review_disabled_preserves_full_source(browsing_client):
    client, archive, config = browsing_client
    body='配置原文\n'+'完整步骤 ' * 1200
    setup_source(client, archive, body)
    preview=client.get('/api/knowledge/from-message', params={'group_id':11,'msg_id':'knowledge-source'})
    assert preview.status_code == 200
    assert preview.json()['text'] == body
    assert preview.json()['date'] == '2026-10-05'  # Pacific/Kiritimati
    saved=client.post('/api/knowledge/from-message', json=save_payload())
    assert saved.status_code == 200, saved.text
    item_id=saved.json()['item_id']
    detail=client.get(f'/api/knowledge/{item_id}').json()
    assert detail['content'] == body
    assert detail['reason'] == '下次排查时参考'
    assert detail['group_id'] == 11 and detail['message_ids'] == ['knowledge-source']
    assert client.get('/api/knowledge').json()['total'] == 1
    assert '完整步骤' in (config.knowledge_dir/'experiences.md').read_text(encoding='utf-8')
    context=client.get(f'/api/knowledge/{item_id}/sources/knowledge-source')
    assert context.status_code == 200 and context.json()['anchor_id'] == 'knowledge-source'
    assert client.get(f'/api/knowledge/{item_id}/sources/unrelated').status_code == 404
    assert client.get('/api/candidates').status_code == 403
    assert client.get('/knowledge').status_code == 200
    assert 'href="/knowledge"' in client.get('/search').text
    hit=client.get('/api/search?q=配置&kind=knowledge').json()['results'][0]
    assert hit['url'] == f'/knowledge?item_id={item_id}'


def test_repeated_source_save_is_idempotent_and_does_not_overwrite_title(browsing_client):
    client, archive, config = browsing_client
    setup_source(client, archive)
    one=client.post('/api/knowledge/from-message', json=save_payload()).json()
    payload=save_payload(); payload['title']='再次点击的新标题'
    two=client.post('/api/knowledge/from-message', json=payload).json()
    assert two['item_id'] == one['item_id'] and two['reused'] is True
    assert client.get('/api/knowledge').json()['total'] == 1
    assert client.get(f"/api/knowledge/{one['item_id']}").json()['title'] == '配置恢复步骤'
    assert (config.knowledge_dir/'experiences.md').read_text(encoding='utf-8').count('条目 ID：') == 1


@pytest.mark.parametrize('status',['pending','later','ignored','confirmed'])
def test_manual_save_uses_original_not_matching_old_ai_candidate(browsing_client,status):
    client,archive,_=browsing_client
    setup_source(client,archive,text='真实原文\n完整步骤')
    candidates=client.app.state.candidates
    cid=candidates.create(group_id=11,created_date='2026-10-05',candidate_type='experience',
        title='配置恢复步骤',link='',reason='旧 AI 理由',content='旧 AI 转述',message_ids=['knowledge-source'])
    candidates.update_status(cid,status)
    result=client.post('/api/knowledge/from-message',json=save_payload())
    assert result.status_code == 200
    detail=client.get(f"/api/knowledge/{result.json()['item_id']}").json()
    assert detail['content'] == '真实原文\n完整步骤'
    assert detail['reason'] == '下次排查时参考'


def test_knowledge_cannot_read_pending_or_unindexed_candidates(browsing_client):
    client, archive, _ = browsing_client
    setup_source(client, archive)
    candidates=client.app.state.candidates
    candidate_id=candidates.create(group_id=11,created_date='2026-10-04',candidate_type='resource',
                                   title='待确认',link='',reason='检查',message_ids=['knowledge-source'])
    assert client.get(f'/api/knowledge/{candidate_id}').status_code == 404
    assert client.get(f'/api/knowledge/{candidate_id}/sources/knowledge-source').status_code == 404
    candidates.confirm(candidate_id)  # status alone is not a persisted knowledge entry
    assert client.get(f'/api/knowledge/{candidate_id}').status_code == 404
    assert client.get('/api/knowledge').json()['total'] == 0
    assert client.get('/api/search?q=待确认&kind=knowledge').json()['total'] == 0


def test_save_rejects_missing_wrong_group_and_invalid_fields(browsing_client):
    client, archive, _ = browsing_client
    setup_source(client, archive)
    for patch in ({'group_id':22}, {'msg_id':'missing'}):
        payload=save_payload();payload.update(patch)
        assert client.post('/api/knowledge/from-message',json=payload).status_code == 404
    for patch in ({'title':'  '}, {'item_type':'unknown'}, {'link':'javascript:alert(1)'},
                  {'group_id':True}, {'group_id':2**64}, {'content':'forged text'}):
        payload=save_payload();payload.update(patch)
        assert client.post('/api/knowledge/from-message',json=payload).status_code == 422
    assert client.get('/api/knowledge?page=0').status_code == 422
    assert client.get('/api/knowledge?item_type=unknown').status_code == 422
    assert client.get('/api/knowledge').json()['total'] == 0


def test_publication_failure_rolls_back_database_and_partial_file(browsing_client, monkeypatch):
    client, archive, config = browsing_client
    setup_source(client, archive)
    path=config.knowledge_dir/'experiences.md';path.parent.mkdir(parents=True)
    path.write_text('已有知识内容\n',encoding='utf-8')
    original=path.read_bytes()
    writer=client.app.state.knowledge; real_write=writer.write
    def fail_after_write(*args,**kwargs):
        real_write(*args,**kwargs)
        raise OSError('synthetic failure')
    monkeypatch.setattr(writer,'write',fail_after_write)
    result=client.post('/api/knowledge/from-message',json=save_payload())
    assert result.status_code == 503
    assert path.read_bytes() == original
    assert archive.connection.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 0
    assert client.get('/api/knowledge').json()['total'] == 0


def test_save_reports_busy_generation_and_can_retry(browsing_client):
    client,archive,_=browsing_client
    setup_source(client,archive)
    with client.app.state.operations.claim('manual_summary'):
        result=client.post('/api/knowledge/from-message',json=save_payload())
        assert result.status_code == 409
    assert client.post('/api/knowledge/from-message',json=save_payload()).status_code == 200


def test_library_lists_existing_reviewed_items_and_paginates(browsing_client):
    client,archive,_=browsing_client
    login(client)
    candidates=client.app.state.candidates
    for i in range(22):
        cid=candidates.create(group_id=11,created_date='2026-09-01',candidate_type='resource',
                              title=f'旧资料 {i}',link='',reason='保留',message_ids=[f'old-{i}'])
        assert client.post(f'/candidates/{cid}/confirm',follow_redirects=False).status_code == 303
    client.app.state.features.update({'review':False},0)
    data=client.get('/api/knowledge').json()
    assert data['total'] == 22 and len(data['items']) == 20
    assert len(client.get('/api/knowledge?page=2').json()['items']) == 2
    assert client.get('/api/knowledge?group_id=22').json()['total'] == 0
    assert client.get('/api/knowledge?item_type=experience').json()['total'] == 0
    assert client.get('/api/knowledge?q=旧资料 21').json()['total'] == 1
    item_id=data['items'][0]['item_id']
    detail=client.get(f'/api/knowledge/{item_id}').json()
    assert detail['missing_source_count'] == 1  # legacy knowledge remains readable
