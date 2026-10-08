import httpx
import pytest

from qq_digest.ai.client import AIClient, AIError
from qq_digest.ai_usage import UsageService


def client_for(usage, handler, retries=3):
    return AIClient(base_url='https://api.example.com',api_key='synthetic-key',model='model',
        usage_service=usage,purpose='summary',transport=httpx.MockTransport(handler),
        max_retries=retries,sleep_fn=lambda _:None)


def ok(content='{"ok":true}',usage=None):
    return httpx.Response(200,json={'choices':[{'message':{'content':content}}],'usage':usage})


def test_client_supports_usage_accounting(tmp_path):
    assert 'usage_service' in __import__('inspect').signature(AIClient).parameters


def test_limit_blocks_retry_before_http_and_records_failed_attempt(tmp_path):
    usage=UsageService(tmp_path/'a.sqlite');usage.update(1,[],0);sent=[]
    def handler(request):sent.append(request);return httpx.Response(500)
    client=client_for(usage,handler)
    with pytest.raises(AIError,match='调用已达上限'):client.chat([{'role':'user','content':'private chat'}])
    assert len(sent)==1
    assert usage.snapshot()['failed']==1 and usage.snapshot()['calls']==1
    client.close()


def test_invalid_json_retry_accounts_usage_and_preserves_business_result(tmp_path):
    usage=UsageService(tmp_path/'a.sqlite');sent=[]
    def handler(request):
        sent.append(request)
        return ok('not json' if len(sent)==1 else '{"ok":true}',{'prompt_tokens':10,'completion_tokens':5})
    client=client_for(usage,handler)
    assert client.chat([])=={'ok':True}
    result=usage.snapshot()
    assert result['calls']==2 and result['failed']==1 and result['successful']==1
    assert result['input_tokens']==20 and result['output_tokens']==10
    client.close()


def test_network_error_and_malformed_response_are_accounted(tmp_path):
    usage=UsageService(tmp_path/'a.sqlite');sent=[]
    def handler(request):
        sent.append(request)
        if len(sent)==1:raise httpx.ReadTimeout('synthetic',request=request)
        return httpx.Response(200,json={'choices':[],'usage':{'prompt_tokens':7,'completion_tokens':1}})
    client=client_for(usage,handler)
    with pytest.raises(AIError):client.chat([])
    result=usage.snapshot()
    assert result['calls']==2 and result['unknown']==1 and result['failed']==1
    assert result['input_tokens']==7 and result['usage_unknown']==1
    client.close()


def test_limit_does_not_trigger_fallback(tmp_path):
    from qq_digest.ai.client import AILimitExceeded
    from qq_digest.ai.bridge import FallbackAIClient
    sent=[]
    class Blocked:
        def chat(self,_):raise AILimitExceeded('调用已达上限')
        def close(self):pass
    class Extra:
        def chat(self,_):sent.append(True);return {}
        def close(self):pass
    with pytest.raises(AILimitExceeded):FallbackAIClient(primary=Blocked(),fallback=Extra()).chat([])
    assert sent==[]


@pytest.mark.parametrize('body',[None,{'choices':[{'message':None}]},{'choices':[{'message':{'content':{}}}]}])
def test_malformed_message_shape_is_recorded_as_failure(tmp_path,body):
    usage=UsageService(tmp_path/'a.sqlite')
    client=client_for(usage,lambda _:httpx.Response(200,json=body))
    with pytest.raises(AIError):client.chat([])
    assert usage.snapshot()['calls']==1 and usage.snapshot()['failed']==1
    client.close()


def test_factory_and_connection_test_share_usage_store(tmp_path,monkeypatch):
    from qq_digest.ai.factory import build_ai_client
    from qq_digest.web.app import _test_deepseek_connection
    from qq_digest.config import Config,AIConfig,SecurityConfig
    cfg=Config(data_dir=tmp_path,archive_path=tmp_path/'a.sqlite',report_dir=tmp_path/'reports',knowledge_dir=tmp_path/'knowledge',
        work_dir=tmp_path/'work',log_dir=tmp_path/'logs',security=SecurityConfig(web_password_hash='fake'),
        ai=AIConfig(base_url='https://api.example.com',model='model',api_key_env='SYNTHETIC_KEY',provider_priority=['compatible']))
    monkeypatch.setenv('SYNTHETIC_KEY','synthetic')
    usage=UsageService(cfg.archive_path);usage.update(1,[],0)
    sent=[]
    def post(self,*args,**kwargs):sent.append(True);return ok(usage={'prompt_tokens':1,'completion_tokens':1})
    monkeypatch.setattr(httpx.Client,'post',post)
    client=build_ai_client(cfg);client.chat([]);client.close()
    with pytest.raises(AIError,match='调用已达上限'):_test_deepseek_connection(cfg)
    assert len(sent)==1


def test_business_invalid_summary_is_failed_with_usage_preserved(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from qq_digest.summary import Summarizer
    from tests.test_summary import message
    usage=UsageService(tmp_path/'a.sqlite')
    client=client_for(usage,lambda _:ok(usage={'prompt_tokens':9,'completion_tokens':2}))
    stamp=datetime(2026,10,8,12,tzinfo=ZoneInfo('Asia/Shanghai'))
    with pytest.raises(ValueError,match='AI响应结构无效'):
        Summarizer(ai=client,max_context_chars=4000).summarize(group_id=123,group_name='合成群',category='general',
            window_start=stamp,window_end=stamp,timezone=ZoneInfo('Asia/Shanghai'),messages=[message('m1',stamp,'合成测试消息')])
    snapshot=usage.snapshot()
    assert snapshot['failed']==1 and snapshot['successful']==0 and snapshot['input_tokens']==9
    client.close()


def test_business_invalid_connection_is_failed(tmp_path,monkeypatch):
    from qq_digest.config import Config,AIConfig,SecurityConfig
    from qq_digest.web.app import _test_deepseek_connection
    cfg=Config(data_dir=tmp_path,archive_path=tmp_path/'a.sqlite',report_dir=tmp_path/'reports',knowledge_dir=tmp_path/'knowledge',
        work_dir=tmp_path/'work',log_dir=tmp_path/'logs',security=SecurityConfig(web_password_hash='fake'),
        ai=AIConfig(base_url='https://api.deepseek.com',model='model',api_key_env='SYNTHETIC_KEY',provider_priority=['compatible']))
    monkeypatch.setenv('SYNTHETIC_KEY','synthetic')
    monkeypatch.setattr(httpx.Client,'post',lambda *args,**kwargs:ok('{"ok":false}',{'prompt_tokens':1,'completion_tokens':2}))
    with pytest.raises(AIError,match='预期响应'):_test_deepseek_connection(cfg)
    assert UsageService(cfg.archive_path).snapshot()['failed']==1


def test_rejecting_exact_response_does_not_reject_later_attempt(tmp_path):
    import json
    from qq_digest.ai.client import reject_response
    usage=UsageService(tmp_path/'a.sqlite')
    client=client_for(usage,lambda _:ok())
    first=client.chat([]);second=client.chat([])
    reject_response(first);reject_response(first)
    assert json.loads(json.dumps(first))=={'ok':True} and second=={'ok':True}
    assert usage.snapshot()['calls']==2 and usage.snapshot()['failed']==1 and usage.snapshot()['successful']==1
    client.close()
