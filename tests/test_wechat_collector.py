from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from qq_digest.archive import Archive
from qq_digest.config import WeChatSource
from qq_digest.collector.wechat import WeChatArchiveCollector, bind_wechat_sources


def source_fixture(tmp_path):
    path=tmp_path/'source.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE binding(id INTEGER PRIMARY KEY,sha256 TEXT)')
        db.execute('INSERT INTO binding VALUES(1,?)',('a'*64,))
        db.execute('''CREATE TABLE archive_messages(platform TEXT,group_id TEXT,shard TEXT,local_id INTEGER,
            server_id TEXT,sender_id TEXT,create_time INTEGER,sort_seq INTEGER,message_type TEXT,content TEXT,raw_json TEXT,collected_at TEXT)''')
        for group,shard,local in [('one@chatroom','a.db',1),('one@chatroom','b.db',1),('other@chatroom','a.db',1)]:
            db.execute('INSERT INTO archive_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('wechat',group,shard,local,'123','wx-member',1795219200,1,'文本','shared synthetic text',json.dumps({'rawlocal_type':1}),'2026-11-21T00:00:00+00:00'))
    return WeChatSource(source_key='wx-source',group_id=-1,native_group_id='one@chatroom',name='Synthetic 微信群',archive_path=path)


def test_collect_preserves_source_identities_and_filters_exact_group(tmp_path):
    source=source_fixture(tmp_path)
    collector=WeChatArchiveCollector(source)
    rows=list(collector.collect(-1,datetime(2026,11,20,tzinfo=timezone.utc),datetime(2026,11,22,tzinfo=timezone.utc)))
    assert len(rows)==2 and len({m.msg_id for m in rows})==2
    assert all(m.sender_qq is None and m.content_json['platform']=='wechat' for m in rows)
    assert all(m.content_json['wechat']['group_id']=='one@chatroom' for m in rows)
    assert all(m.content_json['sender_name']=='wx-member' for m in rows)
    with pytest.raises(ValueError):list(collector.collect(1,datetime.now(timezone.utc),datetime.now(timezone.utc)))


def test_source_is_readonly_and_missing_path_not_created(tmp_path):
    source=source_fixture(tmp_path)
    before=source.archive_path.read_bytes()
    list(WeChatArchiveCollector(source).collect(-1,datetime(2026,11,20,tzinfo=timezone.utc),datetime(2026,11,22,tzinfo=timezone.utc)))
    assert source.archive_path.read_bytes()==before
    missing=source.model_copy(update={'archive_path':tmp_path/'absent.sqlite'})
    with pytest.raises(RuntimeError):list(WeChatArchiveCollector(missing).discover_groups())
    assert not missing.archive_path.exists()


def test_registration_keeps_user_settings_and_deletion(tmp_path):
    source=source_fixture(tmp_path)
    ar=Archive.open(tmp_path/'digest.sqlite')
    bind_wechat_sources(ar,[source])
    ar.connection.execute('UPDATE groups SET daily_summary=0 WHERE group_id=-1');ar.connection.commit()
    bind_wechat_sources(ar,[source])
    assert ar.all_groups()[0].daily_summary is False
    ar.delete_group(-1)
    bind_wechat_sources(ar,[source])
    assert ar.all_groups()==[]
    ar.close()


def test_account_or_native_group_rebind_rejected(tmp_path):
    source=source_fixture(tmp_path);ar=Archive.open(tmp_path/'digest.sqlite')
    bind_wechat_sources(ar,[source])
    with pytest.raises(ValueError):bind_wechat_sources(ar,[source.model_copy(update={'native_group_id':'other@chatroom'})])
    with sqlite3.connect(source.archive_path) as db:db.execute('UPDATE binding SET sha256=?',('b'*64,))
    with pytest.raises(ValueError):bind_wechat_sources(ar,[source])
    ar.close()


def test_normal_ingestion_search_and_context_work_with_internal_group_key(tmp_path):
    from qq_digest.search import search_archive
    from qq_digest.message_context import message_context
    source=source_fixture(tmp_path);ar=Archive.open(tmp_path/'digest.sqlite');bind_wechat_sources(ar,[source])
    messages=list(WeChatArchiveCollector(source).collect(-1,datetime(2026,11,20,tzinfo=timezone.utc),datetime(2026,11,22,tzinfo=timezone.utc)))
    assert ar.ingest(messages).inserted==2
    assert ar.ingest(messages).inserted==0
    result=search_archive(ar,query='synthetic',group_id=-1,kind='message')
    assert result['total']==2
    context=message_context(ar,-1,messages[0].msg_id)
    assert context
    ar.close()


def test_card_text_is_readable_and_never_becomes_executable_html(tmp_path):
    source=source_fixture(tmp_path)
    card='<msg><appmsg><type>5</type><title>&lt;script&gt;bad&lt;/script&gt;</title><des>resource</des><url>https://example.test/a</url></appmsg></msg>'
    with sqlite3.connect(source.archive_path) as db:db.execute('UPDATE archive_messages SET content=?,raw_json=?',(card,json.dumps({'rawlocal_type':49})))
    m=list(WeChatArchiveCollector(source).collect(-1,datetime(2026,11,20,tzinfo=timezone.utc),datetime(2026,11,22,tzinfo=timezone.utc)))[0]
    assert m.message_type=='link' and 'https://example.test/a' in m.text and 'resource' in m.text
    assert '<appmsg>' not in m.text


def test_source_config_rejects_qq_identifier_collision(tmp_path):
    with pytest.raises(ValueError):WeChatSource(source_key='wx',group_id=123,native_group_id='one@chatroom',name='test',archive_path=tmp_path/'source.sqlite')


def test_existing_web_search_group_and_summary_preview_include_wechat(tmp_path):
    from tests.test_web import web_client
    from qq_digest.sync import run_message_sync
    from qq_digest.collector.wechat import bind_wechat_sources
    source=source_fixture(tmp_path)
    client,_,_=web_client.__wrapped__(tmp_path/'web')
    cfg=client.app.state.config;cfg.wechat_sources=[source]
    bind_wechat_sources(client.app.state.archive,[source])
    synced=run_message_sync(config=cfg,refresh=False,group_id=-1,now=datetime(2026,11,22,tzinfo=timezone.utc))
    assert synced.success and synced.messages_inserted==2
    assert client.get('/api/groups').status_code==401
    assert client.post('/login',data={'password':'password123'}).status_code==200
    group=next(g for g in client.get('/api/groups').json()['groups'] if g['group_id']==-1)
    assert group['platform']=='wechat' and group['message_count']==2
    results=client.get('/api/search',params={'group_id':-1,'kind':'message','q':'synthetic'}).json()
    assert results['total']==2
    assert client.get('/groups').status_code==200
    from qq_digest.summary_generation import preview_summary
    from qq_digest.manual_summary import ManualSummaryRequest
    from datetime import date
    preview=preview_summary(client.app.state.archive,ManualSummaryRequest([-1],date(2026,11,21),date(2026,11,21)),'Asia/Shanghai')
    assert preview['total_messages']==2
    client.app.state.archive.close()

def test_wechat_http_entry_points_accept_internal_group_key(tmp_path, monkeypatch):
    from tests.test_web import web_client
    from qq_digest.web import mycortex_routes
    client,_,_=web_client.__wrapped__(tmp_path/'web')
    cfg=client.app.state.config;cfg.wechat_sources=[source_fixture(tmp_path)]
    bind_wechat_sources(client.app.state.archive,cfg.wechat_sources)
    client.post('/login',data={'password':'password123'})
    payload={'group_ids':[-1],'start_date':'2026-11-21','end_date':'2026-11-21'}
    assert client.post('/api/reports/range/preview',json=payload).status_code==200
    assert client.post('/api/collect/batch',json={'group_ids':[-1],'start':'2026-11-21','end':'2026-11-21','refresh':True}).status_code==200
    assert client.get('/api/history-inspection').json()['source_available']
    assert client.post('/api/history-inspection/run',json={'group_ids':[-1],'refresh':False}).status_code==200
    assert client.post('/api/mycortex/v1/submissions',json={'group_id':-1,'message_ids':['synthetic']}).status_code!=422
    assert client.get('/api/mycortex/v1/groups/-1/auto').status_code!=422
    client.app.state.archive.close()


def test_late_upstream_records_are_imported_after_empty_sync(tmp_path):
    from qq_digest.config import Config
    from qq_digest.sync import run_message_sync
    from tests.test_web import web_client
    client,_,_=web_client.__wrapped__(tmp_path/'web');cfg=client.app.state.config
    cfg.wechat_sources=[source_fixture(tmp_path)]
    ar=Archive.open(cfg.archive_path);bind_wechat_sources(ar,cfg.wechat_sources);ar.close()
    source=cfg.wechat_sources[0]
    with sqlite3.connect(source.archive_path) as db:
        saved=list(db.execute('SELECT * FROM archive_messages'));db.execute('DELETE FROM archive_messages')
    now=datetime(2026,11,22,tzinfo=timezone.utc)
    assert run_message_sync(config=cfg,refresh=False,now=now,group_id=-1).success
    with sqlite3.connect(source.archive_path) as db:db.executemany('INSERT INTO archive_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',saved)
    result=run_message_sync(config=cfg,refresh=False,now=now,group_id=-1)
    assert result.success and result.messages_inserted==2


def test_missing_wechat_source_does_not_disable_qq_collector(tmp_path,monkeypatch):
    import qq_digest.application_runner as runner
    from qq_digest.config import Config
    source=source_fixture(tmp_path).model_copy(update={'archive_path':tmp_path/'absent.sqlite'})
    from tests.test_web import web_client
    client,_,_=web_client.__wrapped__(tmp_path/'web');cfg=client.app.state.config
    cfg.wechat_sources=[source]
    collector=runner.build_collector(cfg)
    assert collector.discover_groups()==[]
    with pytest.raises(RuntimeError):list(collector.collect(-1,datetime.now(timezone.utc),datetime.now(timezone.utc)))

def test_wechat_generation_candidate_bookmark_and_mycortex_routes(tmp_path,monkeypatch):
    import re
    from functools import partial
    from qq_digest.ai import factory
    from qq_digest.config import MyCortexConfig
    from qq_digest.mycortex import MyCortexDelivery
    from qq_digest.web import mycortex_routes
    from qq_digest.sync import run_message_sync
    from tests.test_web import web_client
    class AI:
        def chat(self,messages):
            mid=re.search(r'\[([^|\n]+)\|',messages[-1]['content']).group(1)
            return {'group_id':-1,'main_topics':[],'conclusions':[],'resources':[], 'tasks':[],'open_questions':[],
                    'candidates':[{'type':'resource','title':'Synthetic resource','link':'https://example.test','content':'Synthetic','reason':'Reference','message_ids':[mid]}]}
        def close(self):pass
    monkeypatch.setattr(factory,'build_ai_client',lambda cfg:AI())
    client,_,_=web_client.__wrapped__(tmp_path/'web');cfg=client.app.state.config
    cfg.wechat_sources=[source_fixture(tmp_path)];bind_wechat_sources(client.app.state.archive,cfg.wechat_sources)
    assert run_message_sync(config=cfg,group_id=-1,refresh=False,now=datetime(2026,11,22,tzinfo=timezone.utc)).success
    client.post('/login',data={'password':'password123'})
    response=client.post('/api/summaries',json={'group_ids':[-1],'start_date':'2026-11-21','end_date':'2026-11-21'})
    assert response.status_code==200 and len(response.json()['created_reports'])==1,response.text
    ar=client.app.state.archive
    assert ar.connection.execute('SELECT COUNT(*) FROM candidates WHERE group_id=-1').fetchone()[0]==1
    mid=ar.connection.execute('SELECT msg_id FROM messages WHERE group_id=-1 LIMIT 1').fetchone()[0]
    assert client.post('/api/bookmarks',json={'kind':'message','group_id':-1,'msg_id':mid}).status_code==200
    cfg.mycortex=MyCortexConfig(core_dir=tmp_path,store_dir=tmp_path,node_path=tmp_path/'node.exe')
    sent=[]
    def transport(cfg,payload):
        sent.append(payload)
        return {'status':'saved','requestId':payload['requestId'],'id':'d5a78094-7380-4b73-8528-43ce904d9043','revision':1}
    monkeypatch.setattr(mycortex_routes,'MyCortexDelivery',partial(MyCortexDelivery,
        refine=lambda cfg,rows:{'topics':['Synthetic'],'entries':[{'messageId':mid,'summary':'Synthetic reference','subject':'Member','stance':'other-view','quote':'shared synthetic text','timeText':None}]},transport=transport))
    assert client.get('/api/mycortex/v1/groups/-1/auto').json()['enabled'] is False
    saved=client.post('/api/mycortex/v1/submissions',json={'group_id':-1,'message_ids':[mid]})
    assert saved.status_code==200 and saved.json()['status']=='saved',saved.text
    assert sent[0]['title']=='微信 群聊知识'
    assert sent[0]['sourceUri'].startswith('qqdigest://group/-1/message/')
    ar.close()


def test_mixed_batch_keeps_wechat_when_qq_refresh_fails(tmp_path):
    from qq_digest.group_batch import collect_batch
    from tests.test_web import web_client
    client,_,_=web_client.__wrapped__(tmp_path/'web');cfg=client.app.state.config
    cfg.wechat_sources=[source_fixture(tmp_path)];bind_wechat_sources(client.app.state.archive,cfg.wechat_sources)
    def prepare():raise RuntimeError('Synthetic QQ source offline')
    result=collect_batch(config=cfg,group_ids=[123,-1],start=datetime(2026,11,20,tzinfo=timezone.utc),end=datetime(2026,11,22,tzinfo=timezone.utc),prepare=prepare)
    assert [r['status'] for r in result['results']]==['failed','success']
    assert result['results'][1]['inserted']==2
    client.app.state.archive.close()

def test_reused_source_key_is_isolated_and_original_binding_survives(tmp_path):
    from qq_digest.collector.wechat import CombinedCollector
    source=source_fixture(tmp_path);ar=Archive.open(tmp_path/'digest.sqlite')
    bind_wechat_sources(ar,[source])
    changed=source.model_copy(update={'group_id':-2})
    errors=bind_wechat_sources(ar,[changed],strict=False)
    assert -2 in errors
    assert ar.connection.execute('SELECT group_id FROM group_sources').fetchone()[0]==-1
    ar.close()
