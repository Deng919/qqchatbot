import sqlite3
from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.knowledge_library import KnowledgeLibrary
from tests.test_bot_commands import build_handler
import pytest


def test_failed_publisher_cannot_delete_other_committed_entry(tmp_path,monkeypatch):
    ar,service,ids,handler=build_handler(tmp_path,count=2)
    other=Archive.open(tmp_path/'archive.sqlite');other.connection.execute('PRAGMA busy_timeout=1')
    writer=handler.knowledge;path=writer.path_for('resource')
    second=KnowledgeLibrary(other,KnowledgeWriter(writer.targets))
    original=writer.write;attempts=[]
    def interleave(item,kind):
        try:second.confirm_candidate(ids[1]);attempts.append('committed')
        except sqlite3.OperationalError:attempts.append('blocked')
        original(item,kind)
    monkeypatch.setattr(writer,'write',interleave)
    ar.connection.execute(f"CREATE TRIGGER reject_first BEFORE INSERT ON knowledge_items WHEN NEW.candidate_id={ids[0]} BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    ar.connection.commit()
    with pytest.raises(sqlite3.IntegrityError):KnowledgeLibrary(ar,writer).confirm_candidate(ids[0])
    # Competing publisher must be excluded before it writes its file, and can retry afterwards.
    assert attempts==['blocked']
    second.confirm_candidate(ids[1])
    assert service.get(ids[1]).status=='confirmed'
    assert str(ids[1]) in path.read_text(encoding='utf-8')
    assert f'条目 ID：{ids[0]}' not in path.read_text(encoding='utf-8')
    other.close();ar.close()


def test_confirm_rejects_ignored_and_is_idempotent_for_confirmed(tmp_path):
    ar,service,ids,handler=build_handler(tmp_path)
    library=KnowledgeLibrary(ar,handler.knowledge)
    service.update_status(ids[0],'ignored')
    with pytest.raises(ValueError):library.confirm_candidate(ids[0])
    service.update_status(ids[0],'pending');library.confirm_candidate(ids[0])
    revision=service.get(ids[0]).revision
    body=handler.knowledge.path_for('resource').read_bytes()
    library.confirm_candidate(ids[0])
    assert service.get(ids[0]).revision==revision
    assert handler.knowledge.path_for('resource').read_bytes()==body
    ar.close()


def test_manual_source_save_does_not_reuse_generated_identity_after_human_edit(tmp_path):
    from qq_digest.models import GroupConfig,NormalizedMessage
    from datetime import datetime,timezone
    ar=Archive.open(tmp_path/'archive.sqlite');ar.upsert_groups([GroupConfig(group_id=123,name='source')])
    now=datetime(2026,10,14,tzinfo=timezone.utc)
    ar.ingest([NormalizedMessage(group_id=123,msg_id='m1',timestamp=now,collected_at=now,text='original message')])
    service=CandidateService(ar)
    cid=service.create(group_id=123,created_date='2026-10-14',candidate_type='resource',title='original title',reason='AI reason',content='AI body',message_ids=['m1'])
    service.update_details(cid,candidate_type='experience',title='human title',reason='human reason',content='human body',link='',excerpt='')
    library=KnowledgeLibrary(ar,KnowledgeWriter(tmp_path/'knowledge'))
    saved=library.save_message(group_id=123,msg_id='m1',title='original title',item_type='resource',reason='manual reason')
    assert saved['item_id']!=cid
    assert service.get(cid).content=='human body' and service.get(cid).status=='pending'
    assert service.get(saved['item_id']).candidate_type=='resource'
    assert 'original message' in (tmp_path/'knowledge'/'resources.md').read_text(encoding='utf-8')
    ar.close()
