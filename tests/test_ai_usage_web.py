import pytest
from tests.test_web import web_client
from tests.test_cockpit_only import client,setup_env
from qq_digest.ai_usage import usage_for
from tests.test_message_browsing import browsing_client,add_message


def login(client):client.post('/login',data={'password':'password123'})


def test_usage_settings_require_login_and_are_versioned(web_client):
    client,_,_=web_client
    assert client.get('/api/ai-usage').status_code==401
    assert client.put('/api/ai-usage/settings',json={}).status_code==401
    login(client)
    data=client.get('/api/ai-usage').json()
    assert data['settings']['monthly_limit']==0
    payload=dict(monthly_limit=3,rates=[],expected_revision=data['settings']['revision'])
    saved=client.put('/api/ai-usage/settings',json=payload)
    assert saved.status_code==200 and saved.json()['settings']['monthly_limit']==3
    assert client.put('/api/ai-usage/settings',json=payload).status_code==409
    page=client.get('/settings?section=ai').text
    assert 'id="ai-usage-form"' in page and 'id="ai-usage-summary"' in page


@pytest.mark.parametrize('limit',[True,-1,1.5,'2',10**10])
def test_invalid_limits_do_not_change_settings(web_client,limit):
    client,_,_=web_client;login(client)
    response=client.put('/api/ai-usage/settings',json=dict(monthly_limit=limit,rates=[],expected_revision=0))
    assert response.status_code==422
    assert client.get('/api/ai-usage').json()['settings']['revision']==0


def test_usage_saving_obeys_backup_lock_and_active_config(web_client):
    client,_,_=web_client;login(client)
    with client.app.state.operations.claim('backup'):
        assert client.put('/api/ai-usage/settings',json=dict(monthly_limit=2,rates=[],expected_revision=0)).status_code==409
    client.app.state.config=None
    assert client.get('/api/ai-usage').status_code==503


def test_connection_test_reports_limit_without_request(client,monkeypatch,respx_mock):
    cfg=client.app.state.config
    monkeypatch.setenv(cfg.ai.api_key_env,'synthetic')
    cfg=client.app.state.config
    cfg.ai.base_url='https://api.deepseek.com'
    service=usage_for(cfg);service.update(1,[],0)
    attempt=service.reserve(cfg.ai.base_url,cfg.ai.model,'summary',30)
    service.finish(attempt,'success',None)
    route=respx_mock.post(cfg.ai.base_url+'/chat/completions').mock(side_effect=AssertionError('limit must block HTTP'))
    response=client.post('/api/ai-settings/test',json={'expected_revision':client.get('/api/ai-settings').json()['revision']})
    assert response.status_code==429 and '调用已达上限' in response.text
    assert route.call_count==0


def test_report_question_exposes_limit_and_preserves_evidence(web_client,monkeypatch,respx_mock,tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from qq_digest.models import NormalizedMessage
    client,_,_=web_client;login(client)
    cfg=client.app.state.config;cfg.ai.base_url='https://api.deepseek.com'
    monkeypatch.setenv(cfg.ai.api_key_env,'synthetic')
    ar=client.app.state.archive
    stamp=datetime.now(ZoneInfo(cfg.summary.timezone));path=tmp_path/'daily.md';path.write_text('接口发生错误')
    report=ar.record_report(group_id=123,report_date=stamp.date().isoformat(),markdown_path=path,json_path=tmp_path/'daily.json',candidate_ids=[])
    ar.ingest([NormalizedMessage(group_id=123,msg_id='budget-message',timestamp=stamp,collected_at=stamp,text='接口发生错误')])
    usage=usage_for(cfg);usage.update(1,[],0);usage.reserve(cfg.ai.base_url,cfg.ai.model,'summary',30)
    route=respx_mock.post(cfg.ai.base_url+'/chat/completions').mock(side_effect=AssertionError('limit must block HTTP'))
    response=client.post(f'/api/reports/daily/{report}/ask',json={'question':'发生了什么错误？'})
    assert response.status_code==429 and '调用已达上限' in response.text and route.call_count==0
    assert ar.connection.execute('SELECT text FROM messages WHERE msg_id=?',('budget-message',)).fetchone()[0]=='接口发生错误'


def test_archive_question_limit_makes_no_http_request(browsing_client,monkeypatch,respx_mock):
    from datetime import datetime,timezone
    from tests.test_archive_qa_web import enable,payload
    client,ar,cfg=browsing_client;login(client);enable(client)
    cfg.ai.base_url='https://api.deepseek.com';monkeypatch.setenv(cfg.ai.api_key_env,'synthetic')
    add_message(ar,'budget-source',datetime(2026,10,5,12,tzinfo=timezone.utc),text='接口配置方案A')
    service=usage_for(cfg);service.update(1,[],0);service.reserve(cfg.ai.base_url,cfg.ai.model,'summary',30)
    route=respx_mock.post(cfg.ai.base_url+'/chat/completions').mock(side_effect=AssertionError('limit must block HTTP'))
    preview=client.post('/api/archive-qa/preview',json=payload()).json()
    response=client.post('/api/archive-qa/ask',json={'ticket':preview['ticket']})
    assert response.status_code==429 and '调用已达上限' in response.text and route.call_count==0
    assert client.get('/api/archive-qa/sources/'+preview['ticket']+'/s1').status_code==200


def test_archive_invalid_business_json_is_failed(browsing_client,monkeypatch,respx_mock):
    import httpx
    from datetime import datetime,timezone
    from tests.test_archive_qa_web import enable,payload
    client,ar,cfg=browsing_client;login(client);enable(client)
    cfg.ai.base_url='https://api.deepseek.com';monkeypatch.setenv(cfg.ai.api_key_env,'synthetic')
    add_message(ar,'budget-source',datetime(2026,10,5,12,tzinfo=timezone.utc),text='接口配置方案A')
    respx_mock.post(cfg.ai.base_url+'/chat/completions').mock(return_value=httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}],
        'usage':{'prompt_tokens':7,'completion_tokens':1}}))
    preview=client.post('/api/archive-qa/preview',json=payload()).json()
    assert client.post('/api/archive-qa/ask',json={'ticket':preview['ticket']}).status_code==200
    snapshot=usage_for(cfg).snapshot()
    assert snapshot['failed']==1 and snapshot['input_tokens']==7
