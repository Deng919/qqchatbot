from datetime import datetime, timezone
from types import SimpleNamespace

from qq_digest.secondhand_runner import refresh, discover_secondhand
from tests.test_secondhand import market, message, offer


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
