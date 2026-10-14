import pytest
from tests.test_web import web_client


def logged_in(web_client):
    client = web_client[0]
    client.post('/login', data={'password': 'password123'})
    return client


@pytest.mark.parametrize('patch', [{'category':'bad-category'}, {'enabled':'false'},
    {'daily_summary':1}, {'important_candidates':[]}, {'unexpected':True}])
def test_group_patch_rejects_invalid_without_corrupting_group(web_client, patch):
    client=logged_in(web_client)
    before=client.get('/api/groups').json()
    assert client.patch('/api/groups/123',json=patch).status_code==422
    assert client.get('/api/groups').json()==before


def test_group_missing_patch_is_not_success(web_client):
    client=logged_in(web_client)
    assert client.patch('/api/groups/99999',json={'enabled':False}).status_code==404


def test_group_full_form_requires_original_revision(web_client):
    client=logged_in(web_client)
    assert client.patch('/api/groups/123',json={'read_images':True,'category':'study'}).status_code==422


def test_ignored_candidate_confirm_is_business_conflict(web_client):
    client=logged_in(web_client);cid=web_client[1]
    client.app.state.candidates.update_status(cid,'ignored')
    assert client.post(f'/api/candidates/{cid}/confirm',json={}).status_code==409


def test_group_stale_revision_preserves_other_window_changes(web_client):
    client=logged_in(web_client)
    stale=client.get('/api/groups').json()['groups'][0].get('revision',0)
    assert client.patch('/api/groups/123',json={'read_images':True,'expected_revision':stale}).status_code==200
    response=client.patch('/api/groups/123',json={'read_images':False,'keywords':['human'],'expected_revision':stale})
    assert response.status_code==409
    assert client.get('/api/groups').json()['groups'][0]['read_images'] is True


def test_candidate_edit_revision_and_missing_revision_contract(web_client):
    client=logged_in(web_client);cid=web_client[1]
    revision=client.get(f'/api/candidates/{cid}').json().get('revision',0)
    payload={'candidate_type':'resource','title':'saved B','reason':'reason','expected_revision':revision}
    assert client.patch(f'/api/candidates/{cid}',json=payload).status_code==200
    assert client.patch(f'/api/candidates/{cid}',json={**payload,'title':'stale A'}).status_code==409
    assert client.get(f'/api/candidates/{cid}').json()['title']=='saved B'
    payload.pop('expected_revision')
    assert client.patch(f'/api/candidates/{cid}',json=payload).status_code==422


def test_edit_revisions_outside_sqlite_range_are_validation_errors(web_client):
    client=logged_in(web_client);cid=web_client[1]
    assert client.patch('/api/groups/123',json={'enabled':True,'expected_revision':2**63}).status_code==422
    assert client.patch(f'/api/candidates/{cid}',json={'candidate_type':'resource','title':'x','reason':'x','expected_revision':2**63}).status_code==422


def test_candidate_pagination_does_not_hide_older_items(web_client):
    client=logged_in(web_client);service=client.app.state.candidates
    for number in range(24):
        service.create(group_id=123,created_date='2026-10-14',candidate_type='resource',
            title=f'resource {number}',reason='reason',message_ids=[f'm{number}'])
    first=client.get('/api/candidates?page=1&page_size=20').json()
    second=client.get('/api/candidates?page=2&page_size=20').json()
    assert first['total']==second['total']==25
    assert len(first['candidates'])==20 and len(second['candidates'])==5
    assert not ({x['candidate_id'] for x in first['candidates']}&{x['candidate_id'] for x in second['candidates']})


def test_candidate_confirm_and_undo_advance_edit_revision(web_client):
    client=logged_in(web_client);cid=web_client[1]
    before=client.get(f'/api/candidates/{cid}').json()['revision']
    assert client.post(f'/api/candidates/{cid}/confirm',json={}).status_code==200
    assert client.get(f'/api/candidates/{cid}').json()['revision']>before
    assert client.post(f'/api/candidates/{cid}/undo',json={}).status_code==200
    assert client.get(f'/api/candidates/{cid}').json()['revision']>=before+2


@pytest.mark.parametrize('target', ['/candidates?status=later','/tasks?task_id=42'])
def test_login_returns_to_local_original_path(web_client,target):
    client=web_client[0]
    response=client.post('/login',data={'password':'password123','next':target},follow_redirects=False)
    assert response.headers['location']==target


@pytest.mark.parametrize('target',['https://evil.invalid','//evil.invalid','/\\evil.invalid','/login','/logout'])
def test_login_rejects_external_or_looping_return_path(web_client,target):
    response=web_client[0].post('/login',data={'password':'password123','next':target},follow_redirects=False)
    assert response.headers['location']=='/'
