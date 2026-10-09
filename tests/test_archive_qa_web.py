from datetime import datetime, timezone
import pytest
from tests.test_message_browsing import browsing_client,add_message,login


def enable(client):
    state=client.get('/api/features').json()
    assert client.put('/api/features',json={'values':{'archive_qa':True},'expected_revision':state['revision']}).status_code==200


def payload():
    return dict(question='接口配置怎么改？',query='配置',group_id=11,date_from='2026-10-06',date_to='2026-10-06')


def test_qa_auth_switch_preview_and_sources(browsing_client,monkeypatch):
    client,ar,config=browsing_client
    assert client.post('/api/archive-qa/preview',json=payload()).status_code==401
    login(client)
    assert '开启归档问答' in client.get('/archive-qa').text
    assert client.post('/api/archive-qa/preview',json=payload()).status_code==403
    assert 'href="/archive-qa"' in client.get('/search').text
    enable(client)
    add_message(ar,'evidence',datetime(2026,10,5,12,tzinfo=timezone.utc),text='接口配置决定方案A')
    config.ai.api_key_env='ARCHIVE_QA_FAKE_KEY';monkeypatch.setenv('ARCHIVE_QA_FAKE_KEY','synthetic')
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        def chat(self,messages):calls.append(messages);return {'answer':'方案A','source_ids':['s1']}
        def close(self):calls.append('closed')
    monkeypatch.setattr('qq_digest.web.archive_qa_routes.AIClient',Client)
    data=client.post('/api/archive-qa/preview',json=payload()).json()
    answer=client.post('/api/archive-qa/ask',json={'ticket':data['ticket']})
    assert answer.status_code==200 and answer.json()['sources'][0]['group_id']==11
    assert calls[-1]=='closed'
    assert client.post('/api/archive-qa/ask',json={'ticket':data['ticket']}).json()==answer.json()
    assert len(calls)==2
    source=client.get('/api/archive-qa/sources/'+data['ticket']+'/s1')
    assert source.status_code==200 and source.json()['msg_id']=='evidence'
    assert client.get('/api/archive-qa/sources/'+data['ticket']+'/s999').status_code==410
    ar.connection.execute('DELETE FROM messages');ar.connection.commit()
    assert client.get('/api/archive-qa/sources/'+data['ticket']+'/s1').status_code==409


def test_no_messages_need_no_key_or_paid_client(browsing_client,monkeypatch):
    client,ar,config=browsing_client;login(client);enable(client)
    def forbidden(**kwargs):raise AssertionError('Must not construct paid client without evidence')
    monkeypatch.setattr('qq_digest.web.archive_qa_routes.AIClient',forbidden)
    data=client.post('/api/archive-qa/preview',json=payload()).json()
    result=client.post('/api/archive-qa/ask',json={'ticket':data['ticket']})
    assert result.status_code==200 and '无法确认' in result.json()['answer']


def test_validation_and_operation_lock(browsing_client):
    client,ar,_=browsing_client;login(client);enable(client)
    assert client.post('/api/archive-qa/preview',json={**payload(),'date_from':'bad'}).status_code==422
    assert client.post('/api/archive-qa/ask',json={'ticket':'x'*30,'question':'unexpected'}).status_code==422
    with client.app.state.operations.claim('backup'):
        assert client.post('/api/archive-qa/preview',json=payload()).status_code==409


def test_independent_same_second_logins_cannot_share_evidence(browsing_client,monkeypatch):
    client,ar,_=browsing_client
    monkeypatch.setattr('qq_digest.web.auth.time.time',lambda:1791230000)
    login(client);enable(client)
    add_message(ar,'private-evidence',datetime(2026,10,5,12,tzinfo=timezone.utc),text='接口配置方案A')
    ticket=client.post('/api/archive-qa/preview',json=payload()).json()['ticket']
    first_cookie=client.cookies.get('qq_digest_session')
    login(client)
    assert client.cookies.get('qq_digest_session')!=first_cookie
    assert client.get('/api/archive-qa/sources/'+ticket+'/s1').status_code==410


@pytest.mark.parametrize('failure',['missing_config','missing_key','public_url'])
def test_configuration_errors_are_generic_service_errors(browsing_client,monkeypatch,failure):
    from qq_digest.config import ConfigError
    client,ar,config=browsing_client;login(client);enable(client)
    def forbidden(**kwargs):raise AssertionError('Configuration failure must not construct a network client')
    monkeypatch.setattr('qq_digest.web.archive_qa_routes.AIClient',forbidden)
    add_message(ar,'evidence',datetime(2026,10,5,12,tzinfo=timezone.utc),text='接口配置方案A')
    ticket=client.post('/api/archive-qa/preview',json=payload()).json()['ticket']
    if failure=='missing_config':client.app.state.config=None
    elif failure=='missing_key':
        def missing_key(self):raise ConfigError('synthetic missing key')
        monkeypatch.setattr(type(config),'resolve_api_key',missing_key)
    else:monkeypatch.setattr('qq_digest.web.archive_qa_routes.is_public_distribution',lambda:True)
    result=client.post('/api/archive-qa/ask',json={'ticket':ticket})
    assert result.status_code==503
    assert result.json()['detail']=='问答暂不可用，请检查 DeepSeek API Key 和网络后重试'


def test_page_uses_configured_timezone_and_search_navigation(browsing_client):
    client,_,_=browsing_client;login(client);enable(client)
    page=client.get('/archive-qa').text
    assert 'Pacific/Kiritimati' in page
    assert 'id="nav-search" aria-current="page"' in page


def test_preview_endpoint_is_read_only_with_unread_attachments(browsing_client, monkeypatch):
    from qq_digest.models import GroupConfig, NormalizedMessage
    client, ar, _ = browsing_client
    login(client); enable(client)
    ar.upsert_groups([GroupConfig(group_id=11, name='研发群', read_images=True)])
    stamp = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    add_message(ar, 'evidence', stamp, text='接口配置采用方案A')
    ar.ingest([NormalizedMessage(msg_id='unread', group_id=11, timestamp=stamp,
        collected_at=stamp, text='无关图片', content_json={'attachments': [{'key': 'image', 'kind': 'image'}]})])
    def forbidden(*args, **kwargs):
        raise AssertionError('Preview must not read attachments or invoke AI')
    monkeypatch.setattr('qq_digest.attachments.AttachmentService.prepare', forbidden)
    monkeypatch.setattr('qq_digest.collector.ntqq.NTQQCollector.collect', forbidden)
    monkeypatch.setattr('qq_digest.web.archive_qa_routes.AIClient', forbidden)
    before = ar.connection.total_changes

    result = client.post('/api/archive-qa/preview', json=payload())

    assert result.status_code == 200
    assert [row['msg_id'] for row in result.json()['messages']] == ['evidence']
    assert ar.connection.total_changes == before
    assert '只查询归档文字和已有附件识别缓存' in client.get('/archive-qa').text
