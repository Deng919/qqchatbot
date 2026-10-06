from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.archive_qa import ArchiveQAService


@pytest.fixture
def service(tmp_path):
    ar=Archive.open(tmp_path/'qa.sqlite')
    ar.upsert_groups([GroupConfig(group_id=11,name='研发'),GroupConfig(group_id=22,name='产品')])
    for gid,mid,text,stamp in [(11,'same','接口配置决定使用方案A','2026-10-05T16:00:00+00:00'),
        (22,'same','接口配置决定使用方案B','2026-10-05T16:00:00+00:00'),
        (11,'old','接口配置旧方案C','2026-10-05T15:59:59+00:00')]:
        now=datetime.fromisoformat(stamp)
        ar.ingest([NormalizedMessage(msg_id=mid,group_id=gid,text=text,timestamp=now,collected_at=now)])
    svc=ArchiveQAService(ar)
    yield svc
    ar.close()


def preview(service, **kwargs):
    return service.preview(owner='session',question='接口配置怎么改？',query='接口配置',
        group_id=11,date_from='2026-10-06',date_to='2026-10-06',**kwargs)


class FakeAI:
    def __init__(self,response=None):self.response=response or {'answer':'使用方案A','source_ids':['s1']};self.calls=[]
    def chat(self,messages):self.calls.append(messages);return self.response


def test_preview_and_prompt_are_strictly_scoped(service):
    data=preview(service)
    assert data['matched_count']==1
    assert data['messages'][0]['group_id']==11
    ai=FakeAI();answer=service.ask(data['ticket'],'session',ai)
    assert answer['sources'][0]['msg_id']=='same' and answer['sources'][0]['group_id']==11
    sent=str(ai.calls)
    assert '方案A' in sent and '方案B' not in sent and '方案C' not in sent
    assert service.source(data['ticket'],'session','s1')['text']=='接口配置决定使用方案A'


def test_zero_evidence_and_invalid_citations_cannot_assert_an_answer(service):
    data=service.preview(owner='session',question='卫星发射成功了吗？',query='卫星',group_id=11,date_from='2026-10-06',date_to='2026-10-06')
    ai=FakeAI();answer=service.ask(data['ticket'],'session',ai)
    assert not ai.calls and not answer['sources'] and '无法确认' in answer['answer']
    data=preview(service)
    answer=service.ask(data['ticket'],'session',FakeAI({'answer':'错引其他群','source_ids':['same','s999',{},None]}))
    assert not answer['sources'] and '无法确认' in answer['answer']


def test_preview_bound_to_session_and_content_and_expires(service):
    data=preview(service)
    with pytest.raises(LookupError):service.ask(data['ticket'],'other',FakeAI())
    service.archive.connection.execute("UPDATE messages SET text='归档已变更' WHERE group_id=11 AND msg_id='same'")
    service.archive.connection.commit()
    ai=FakeAI()
    with pytest.raises(ValueError,match='变化'):service.ask(data['ticket'],'session',ai)
    assert not ai.calls
    service.clock=lambda:10**20
    with pytest.raises(LookupError):service.source(data['ticket'],'session','s1')


def test_same_ticket_cached_and_duplicate_inflight_rejected(service):
    data=preview(service)
    class Concurrent(FakeAI):
        def chat(self,messages):
            with pytest.raises(ValueError,match='正在'):
                service.ask(data['ticket'],'session',self)
            return super().chat(messages)
    ai=Concurrent();first=service.ask(data['ticket'],'session',ai)
    assert service.ask(data['ticket'],'session',ai)==first and len(ai.calls)==1


def test_all_groups_duplicate_ids_have_independent_sources(service):
    data=service.preview(owner='session',question='接口配置？',query='接口配置',group_id=None,date_from='2026-10-06',date_to='2026-10-06')
    assert data['matched_count']==2 and len({m['source_id'] for m in data['messages']})==2
    answer=service.ask(data['ticket'],'session',FakeAI({'answer':'两群不同','source_ids':['s1','s2']}))
    assert {m['group_id'] for m in answer['sources']}=={11,22}


@pytest.mark.parametrize('patch', [dict(group_id=99),dict(date_from='2026-10-07'),dict(question=' '),dict(query='x')])
def test_invalid_scope_and_input_rejected(service,patch):
    params=dict(owner='session',question='配置？',query='配置',group_id=11,date_from='2026-10-06',date_to='2026-10-06');params.update(patch)
    with pytest.raises((ValueError,LookupError)):service.preview(**params)
