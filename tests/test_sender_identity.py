import json
import sqlite3
from datetime import datetime,timezone

import pytest

from qq_digest.sender_identity import SenderIdentityResolver, enrich_archive_senders, sender_fields
from qq_digest.archive import Archive
from qq_digest.models import GroupConfig,NormalizedMessage
from qq_digest.message_context import message_context
from tests.test_ntqq_collector import ntqq_dbs,ntqq_fts_dbs


@pytest.fixture
def identities(tmp_path):
    with sqlite3.connect(tmp_path/'group_info.db') as con:
        con.execute('CREATE TABLE group_member3 ("60001" INTEGER,"1000" TEXT,"1002" INTEGER,"64003" TEXT,"20002" TEXT)')
        con.executemany('INSERT INTO group_member3 VALUES (?,?,?,?,?)',[(11,'u_a',12345,'本群名片','昵称'),(22,'u_a',12345,'别群名片','昵称'),(11,'u_b',23456,'','昵称B')])
    with sqlite3.connect(tmp_path/'profile_info.db') as con:
        con.execute('CREATE TABLE profile_info_v6 ("1000" TEXT,"1002" INTEGER,"20002" TEXT)')
        con.executemany('INSERT INTO profile_info_v6 VALUES (?,?,?)',[('u_a',12345,'个人昵称'),('u_c',34567,'昵称C')])
    with sqlite3.connect(tmp_path/'nt_msg.db') as con:
        con.execute('CREATE TABLE nt_uid_mapping_table ("48902" TEXT,"1002" INTEGER)')
        con.execute('INSERT INTO nt_uid_mapping_table VALUES (?,?)',('u_d',45678))
    return tmp_path


def test_group_card_nickname_and_numeric_fallback(identities):
    resolver=SenderIdentityResolver(identities)
    assert resolver.resolve(11,'u_a')['sender_name']=='本群名片'
    assert resolver.resolve(22,'u_a')['sender_name']=='别群名片'
    assert resolver.resolve(99,'u_a')['sender_name']=='个人昵称'
    assert resolver.resolve(11,'u_b')['sender_name']=='昵称B'
    assert resolver.resolve(11,'u_c')['sender_qq']==34567
    assert resolver.resolve(11,'u_d')=={'sender_qq':45678}
    assert resolver.resolve(11,'',12345)['sender_name']=='本群名片'
    assert resolver.resolve(11,'u_unknown')=={}


def test_conflicting_uid_or_qq_does_not_assign_a_person(identities):
    with sqlite3.connect(identities/'profile_info.db') as con:con.execute('INSERT INTO profile_info_v6 VALUES (?,?,?)',('u_a',99999,'错误资料'))
    assert SenderIdentityResolver(identities).resolve(11,'u_a')=={}
    assert SenderIdentityResolver(identities).resolve(11,'u_b',98765)=={'sender_qq':98765}


def test_optional_missing_or_corrupt_profile_is_safe(tmp_path):
    (tmp_path/'profile_info.db').write_bytes(b'not SQLite')
    assert SenderIdentityResolver(tmp_path).resolve(11,'',789)=={'sender_qq':789}
    assert not (tmp_path/'group_info.db').exists()


def test_enrich_existing_messages_is_idempotent_and_preserves_content(identities,tmp_path):
    ar=Archive.open(tmp_path/'archive.sqlite');ar.upsert_groups([GroupConfig(group_id=11,name='合成群')])
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    ar.ingest([NormalizedMessage(msg_id='one',group_id=11,text='正文不变',timestamp=now,collected_at=now,content_json={'sender_uid':'u_a','other':True})])
    before=dict(ar.connection.execute('SELECT * FROM messages').fetchone())
    assert enrich_archive_senders(ar,SenderIdentityResolver(identities))==1
    assert enrich_archive_senders(ar,SenderIdentityResolver(identities))==0
    after=dict(ar.connection.execute('SELECT * FROM messages').fetchone())
    assert {k:v for k,v in before.items() if k not in ('sender_qq','content_json')}=={k:v for k,v in after.items() if k not in ('sender_qq','content_json')}
    assert json.loads(after['content_json'])['other'] is True
    assert sender_fields(after)=={'sender_name':'本群名片','sender_label':'本群名片 · 12345'}
    assert message_context(ar,11,'one')['messages'][0]['sender_label']=='本群名片 · 12345'
    ar.close()


def test_display_ignores_malformed_metadata():
    for raw in ('bad','[]','null','{"sender_name":{}}'):
        assert sender_fields({'sender_qq':123,'content_json':raw})=={}


def test_resolver_does_not_change_source_databases(identities):
    before={p:p.read_bytes() for p in identities.glob('*.db')}
    SenderIdentityResolver(identities).resolve(11,'u_a')
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_reimport_only_refreshes_identity_and_preserves_original_message(identities,tmp_path):
    ar=Archive.open(tmp_path/'duplicate.sqlite');ar.upsert_groups([GroupConfig(group_id=11,name='合成群')])
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    message=NormalizedMessage(msg_id='same',group_id=11,text='旧正文',timestamp=now,collected_at=now,content_json={'sender_uid':'u_a','original':True})
    ar.ingest([message]);resolved=SenderIdentityResolver(identities).enrich(message.model_copy(update={'text':'不能覆盖的新正文'}))
    result=ar.ingest([resolved]);row=ar.connection.execute('SELECT * FROM messages').fetchone()
    assert (result.inserted,result.skipped)==(0,1)
    assert row['text']=='旧正文' and json.loads(row['content_json'])['original'] is True
    assert sender_fields(row)['sender_name']=='本群名片'
    assert row['sender_qq']==12345
    ar.close()


def test_current_fts_collection_resolves_group_member(ntqq_fts_dbs):
    from qq_digest.collector.ntqq import NTQQCollector
    with sqlite3.connect(ntqq_fts_dbs/'group_info.db') as con:
        con.execute('CREATE TABLE group_member3 ("60001" INTEGER,"1000" TEXT,"1002" INTEGER,"64003" TEXT,"20002" TEXT)')
        con.execute('INSERT INTO group_member3 VALUES (100,\'u_sender\',45678,\'合成名片\',\'合成昵称\')')
    rows=list(NTQQCollector(ntqq_fts_dbs).collect(100,datetime(2025,10,7,tzinfo=timezone.utc),datetime(2025,10,8,tzinfo=timezone.utc)))
    assert rows[0].sender_qq==45678 and rows[0].content_json['sender_name']=='合成名片'


def test_modern_legacy_uses_sender_column_instead_of_group_number(ntqq_dbs):
    from qq_digest.collector.ntqq import NTQQCollector
    with sqlite3.connect(ntqq_dbs/'nt_msg.db') as con:
        con.execute('ALTER TABLE group_msg_table ADD COLUMN "40033" INTEGER DEFAULT 0')
        con.execute('UPDATE group_msg_table SET "40027"=100,"40033"=45678,"40020"=\'u_sender\' WHERE "40021"=\'100\'')
    rows=list(NTQQCollector(ntqq_dbs).collect(100,datetime(2025,10,7,tzinfo=timezone.utc),datetime(2025,10,8,tzinfo=timezone.utc)))
    assert rows[0].sender_qq==45678 and rows[0].content_json['sender_uid']=='u_sender'


def test_search_browsing_and_qa_sources_share_safe_identity(identities,tmp_path):
    from qq_digest.message_browsing import browse_messages
    from qq_digest.search import search_archive
    from qq_digest.archive_qa import ArchiveQAService
    ar=Archive.open(tmp_path/'surfaces.sqlite');ar.upsert_groups([GroupConfig(group_id=11,name='合成群')])
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    ar.ingest([NormalizedMessage(msg_id='one',group_id=11,text='接口配置方案',timestamp=now,collected_at=now,content_json={'sender_uid':'u_a'})])
    enrich_archive_senders(ar,SenderIdentityResolver(identities))
    assert search_archive(ar,query='接口',kind='message')['results'][0]['sender_label']=='本群名片 · 12345'
    assert browse_messages(ar,date_from='2026-10-07',date_to='2026-10-07')['results'][0]['sender_name']=='本群名片'
    service=ArchiveQAService(ar);data=service.preview(owner='test',question='接口配置？',query='接口',group_id=11,date_from='2026-10-07',date_to='2026-10-07')
    assert data['messages'][0]['sender_label']=='本群名片 · 12345'
    assert 'content_json' not in data['messages'][0]
    ar.connection.execute('UPDATE messages SET content_json=?',('{"sender_name":"修改名称"}',));ar.connection.commit()
    with pytest.raises(ValueError,match='变化'):service.source(data['ticket'],'test','s1')
    ar.close()


def test_archive_qa_templates_are_packaged(tmp_path):
    from scripts.build_desktop import build_command
    command=build_command(tmp_path)
    for name in ('archive_qa.html','archive_qa_script.html','archive_qa_style.html'):
        assert any(value.endswith(name+';qq_digest/web/templates') for value in command)


def test_reimport_preserves_qq_and_rejects_conflicting_identity(tmp_path):
    ar=Archive.open(tmp_path/'refresh.sqlite');ar.upsert_groups([GroupConfig(group_id=11,name='合成群')])
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    message=NormalizedMessage(msg_id='one',group_id=11,text='正文',timestamp=now,collected_at=now,sender_qq=12345,content_json={'sender_uid':'u_a','sender_name':'旧名片'})
    ar.ingest([message])
    ar.ingest([message.model_copy(update={'sender_qq':None,'content_json':{'sender_uid':'u_a','sender_name':'新名片'}})])
    row=ar.connection.execute('SELECT * FROM messages').fetchone()
    assert sender_fields(row)['sender_label']=='新名片 · 12345'
    for metadata in ({'sender_uid':'u_a'},{'sender_uid':'u_b','sender_name':'别人'}):
        ar.ingest([message.model_copy(update={'sender_qq':23456,'content_json':metadata})])
        row=ar.connection.execute('SELECT * FROM messages').fetchone()
        assert sender_fields(row)['sender_label']=='新名片 · 12345'
    ar.close()


def test_reimport_mapping_only_adds_qq_without_a_name(tmp_path):
    ar=Archive.open(tmp_path/'mapping.sqlite');ar.upsert_groups([GroupConfig(group_id=11,name='合成群')])
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    message=NormalizedMessage(msg_id='one',group_id=11,text='正文',timestamp=now,collected_at=now,content_json={'sender_uid':'u_a'})
    ar.ingest([message]);ar.ingest([message.model_copy(update={'sender_qq':12345})])
    assert ar.connection.execute('SELECT sender_qq FROM messages').fetchone()[0]==12345
    ar.close()


def test_report_question_source_displays_identity(tmp_path):
    from qq_digest.report_qa import ReportEvidence,answer_report_question
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    message=NormalizedMessage(msg_id='one',group_id=11,text='正文',timestamp=now,collected_at=now,sender_qq=12345,content_json={'sender_name':'本群名片'})
    class FakeClient:
        def chat(self,prompt):return {'answer':'合成回答','source_ids':['one']}
    result=answer_report_question(ReportEvidence(11,'2026-10-07','2026-10-07',tmp_path/'missing.md',[message]),'谁发的？',[],FakeClient())
    assert result['sources'][0]['sender_label']=='本群名片 · 12345'
    assert 'content_json' not in result['sources'][0]
