from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from qq_digest.secondhand_runner import refresh, discover_secondhand, accept_batch
from tests.test_secondhand import market, message, offer, ingest


class AI:
    calls=[]
    def chat(self,messages):
        import json
        units=json.loads(messages[-1]['content'])['sources'];self.calls.append(units)
        return {'items':[offer(unit) for unit in units if 'U2720' in unit['text']]}
    def close(self):pass


def test_refresh_idempotent_and_block_before_ai(market):
    market.archive.ingest([message(),message(mid='b',sender=200)])
    market.block(200);ai=AI();ai.calls=[]
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False))
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=ai)
    assert result['processed']==1 and len(ai.calls)==1
    assert all(unit['seller_qq']!=200 for unit in ai.calls[0])
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=ai)
    assert result['processed']==0 and len(ai.calls)==1


def test_unavailable_forward_is_visible_and_not_sent_to_ai(market):
    market.archive.ingest([message(kind='forward',text='[转发]')])
    ai=AI();ai.calls=[]
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False))
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=ai)
    assert result['unavailable']==1 and ai.calls==[]
    assert market.db.execute('SELECT state FROM market_scans').fetchone()[0]=='forward_unavailable'


def test_discovery_matches_secondhand_names_only():
    groups=[{'group_id':1,'name':'Hust二手交易1群'},{'group_id':2,'name':'华科二手书群'},{'group_id':3,'name':'Python学习'}]
    assert [row['group_id'] for row in discover_secondhand(groups)]==[1,2]


def test_ai_failure_keeps_prior_items_and_can_retry(market):
    unit=__import__('qq_digest.secondhand_evidence',fromlist=['source_units']).source_units([message()])[0]
    market.archive.ingest([message()]);market.accept_units([unit],{'items':[offer(unit)]})
    market.archive.ingest([message(mid='new',text='出售笔记本电脑2000元')])
    class Broken:
        def chat(self,_):raise RuntimeError('synthetic')
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False))
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=Broken())
    assert result['failed'] and market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==1


def test_forward_image_reads_after_original_seller_block_filter(market,tmp_path,monkeypatch):
    import hashlib
    from qq_digest.models import GroupConfig
    from qq_digest.attachments import AttachmentService
    market.archive.upsert_groups([GroupConfig(group_id=11,name='二手1',read_images=True)])
    path=tmp_path/'goods.png';path.write_bytes(b'verified test bytes')
    ref={'key':'image1','kind':'image','name':'goods.png','path':str(path),'md5':hashlib.md5(path.read_bytes()).hexdigest()}
    nodes=[{'sender_qq':200,'timestamp':'2026-10-18T10:00:00+08:00','text':'[图片]','attachments':[ref]},
           {'sender_qq':300,'timestamp':'2026-10-18T10:00:00+08:00','text':'[图片]','attachments':[ref]}]
    market.archive.ingest([message(kind='forward',metadata={'forward_messages':nodes})]);market.block(200)
    reads=[]
    monkeypatch.setattr(AttachmentService,'_read',lambda self,ref:(reads.append(ref) or '出戴尔U2720显示器，完好带底座，价格800元',False))
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False),ai=object())
    ai=AI();ai.calls=[]
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=ai)
    assert len(reads)==1 and result['offers']==1
    assert ai.calls[0][0]['seller_qq']==300


def test_bad_item_does_not_fail_unrelated_sources(market):
    market.archive.ingest([message(),message(mid='bad',sender=200)])
    class PartlyInvalid:
        def chat(self,messages):
            import json
            units=json.loads(messages[-1]['content'])['sources']
            rows=[offer(unit) for unit in units];rows[1]['evidence']='invented'
            return {'items':rows}
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False))
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=PartlyInvalid())
    assert result['processed']==1 and result['offers']==1 and result['failed']==1
    assert market.db.execute('SELECT count(*) FROM market_scans').fetchone()[0]==1


def test_conflicting_duplicate_source_keeps_old_listing_and_accepts_other_sources(market):
    good,bad=ingest(market,[message(),message(mid='bad',sender=200)])
    original=offer(bad)
    market.accept_units([bad],{'items':[original]},record_scan=False)
    before=tuple(market.db.execute('SELECT * FROM market_posts').fetchone())
    offers,processed,failed,reasons=accept_batch(market,[good,bad],{
        'items':[offer(good),original,dict(original,condition='')],
    })
    assert (offers,processed,failed)==(1,1,1) and any('冲突' in reason for reason in reasons)
    assert tuple(market.db.execute('SELECT * FROM market_posts WHERE source_key=?',(bad['key'],)).fetchone())==before
    assert market.db.execute('SELECT count(*) FROM market_posts').fetchone()[0]==2
    assert [row['source_key'] for row in market.db.execute('SELECT * FROM market_scans')]==[good['key']]


def test_recovered_image_removes_only_its_derived_failure(market,tmp_path,monkeypatch):
    import hashlib
    from qq_digest.models import GroupConfig
    from qq_digest.attachments import AttachmentService
    market.archive.upsert_groups([GroupConfig(group_id=11,name='二手1',read_images=True)])
    path=tmp_path/'goods.png';path.write_bytes(b'test')
    ref={'key':'image','kind':'image','name':path.name,'path':str(path),'md5':hashlib.md5(path.read_bytes()).hexdigest()}
    market.archive.ingest([message(text='出戴尔U2720显示器，完好带底座，价格800元',metadata={'attachments':[ref]})])
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False),ai=object())
    def missing(self,ref):raise FileNotFoundError()
    monkeypatch.setattr(AttachmentService,'_read',missing)
    first=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=AI())
    assert first['unavailable']==1
    monkeypatch.setattr(AttachmentService,'_read',lambda *args:('显示器原图',False))
    second=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=AI())
    assert second['unavailable']==0
    assert market.db.execute("SELECT count(*) FROM market_scans WHERE state!='done'").fetchone()[0]==0


def test_force_refresh_reprocesses_done_sources(market):
    market.archive.ingest([message()]);ai=AI();ai.calls=[]
    config=SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'),ntqq=SimpleNamespace(enabled=False))
    refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=ai)
    result=refresh(config,market.archive,'2026-10-13','2026-10-19',collect=False,ai=ai,force=True)
    assert len(ai.calls)==2 and result['processed']==1


@pytest.mark.parametrize('response_kind,failed_sources,stored_offers', [
    ('valid', 0, 2), ('invalid', 2, 0), ('partial', 1, 1), ('malformed', 2, 0),
    ('invalid_source_type', 2, 0), ('invalid_direction_type', 1, 1),
])
def test_market_business_validation_updates_exact_api_attempt_usage(
    market, tmp_path, response_kind, failed_sources, stored_offers,
):
    import json
    from qq_digest.ai.client import AIClient
    from qq_digest.ai_usage import UsageService

    market.archive.ingest([message(), message(mid='b', sender=200)])
    usage = UsageService(tmp_path / 'usage.sqlite')

    def respond(request):
        units = json.loads(json.loads(request.content)['messages'][-1]['content'])['sources']
        rows = [offer(unit) for unit in units]
        if response_kind == 'invalid':
            for row in rows:
                row['evidence'] = 'invented'
        elif response_kind == 'partial':
            rows[1]['evidence'] = 'invented'
        elif response_kind == 'invalid_source_type':
            rows[0]['source_key'] = []
        elif response_kind == 'invalid_direction_type':
            rows[1]['direction'] = []
        payload = {'items': 'invalid'} if response_kind == 'malformed' else {'items': rows}
        return httpx.Response(200, json={
            'choices': [{'message': {'content': json.dumps(payload)}}],
            'usage': {'prompt_tokens': 17, 'completion_tokens': 9},
        })

    ai = AIClient(base_url='https://example.com/v1', api_key='synthetic', model='test',
                  transport=httpx.MockTransport(respond), max_retries=1, usage_service=usage)
    config = SimpleNamespace(summary=SimpleNamespace(timezone='Asia/Shanghai'))
    try:
        result = refresh(config, market.archive, '2026-10-13', '2026-10-19', collect=False, ai=ai)
    finally:
        ai.close()

    assert result['failed'] == failed_sources and result['offers'] == stored_offers
    assert market.db.execute('SELECT count(*) FROM market_posts').fetchone()[0] == stored_offers
    snapshot = usage.snapshot()
    assert snapshot['calls'] == 1
    assert snapshot['failed'] == int(failed_sources > 0)
    assert snapshot['successful'] == int(failed_sources == 0)
    assert snapshot['input_tokens'] == 17 and snapshot['output_tokens'] == 9
