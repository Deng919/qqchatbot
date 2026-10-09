from datetime import datetime,timezone
from types import SimpleNamespace

from qq_digest.pipeline import DailyPipeline
from qq_digest.manual_summary import ManualSummaryService,ManualSummaryRequest
from qq_digest.archive_qa import ArchiveQAService
from qq_digest.report_qa import load_report_evidence
from tests.test_attachments import setup_message
from qq_digest.collector.base import Collector
from qq_digest.attachments import AttachmentService


class EmptyCollector(Collector):
    def collect(self,*args):return []
    def discover_groups(self):return []


class SummaryAI:
    def __init__(self):self.inputs=[]
    def chat(self,messages):
        self.inputs.append(messages)
        return {'group_id':11,'overview':'附件概览','main_topics':[],'conclusions':[],'resources':[],'tasks':[],'open_questions':[],'candidates':[]}


def test_daily_and_range_use_attachment_text(tmp_path):
    ar,msg,_=setup_message(tmp_path);ai=SummaryAI()
    run=DailyPipeline(archive=ar,collector=EmptyCollector(),ai_client=ai,report_dir=tmp_path/'reports',max_context_chars=60000,timezone_name='UTC')
    run.run_daily(datetime.now(timezone.utc))
    assert 'hello attachment' in str(ai.inputs[0])
    manual=ManualSummaryService(archive=ar,ai_client=ai,report_dir=tmp_path/'reports',max_context_chars=60000,timezone_name='UTC')
    manual.run(ManualSummaryRequest(group_ids=[11],start_date=msg.timestamp.date(),end_date=msg.timestamp.date()))
    assert 'hello attachment' in str(ai.inputs[-1])


def test_archive_questions_find_attachment_contents_and_invalidate_closed_policy(tmp_path):
    ar,msg,_=setup_message(tmp_path);AttachmentService(ar).prepare([msg])
    service=ArchiveQAService(ar,timezone_name='UTC')
    preview=service.preview(owner='test',question='hello attachment',query='hello',group_id=11,date_from=msg.timestamp.date().isoformat(),date_to=msg.timestamp.date().isoformat())
    assert preview['messages'] and 'hello attachment' in preview['messages'][0]['excerpt']
    ar.connection.execute('UPDATE groups SET read_files=0 WHERE group_id=11');ar.connection.commit()
    import pytest
    with pytest.raises(ValueError):service.ask(preview['ticket'],'test',SummaryAI())


def test_archive_questions_find_cached_mixed_text_attachment_messages(tmp_path, monkeypatch):
    ar,msg,_=setup_message(tmp_path)
    ar.connection.execute("UPDATE messages SET message_type='text' WHERE msg_id=?",(msg.msg_id,));ar.connection.commit()
    AttachmentService(ar).prepare([msg])
    def forbidden(*args, **kwargs):
        raise AssertionError('Archive preview must not prepare attachments')
    monkeypatch.setattr(AttachmentService, 'prepare', forbidden)
    service=ArchiveQAService(ar,timezone_name='UTC',attachment_config=SimpleNamespace(ntqq=SimpleNamespace(enabled=False)))
    preview=service.preview(owner='test',question='hello attachment',query='hello',group_id=11,date_from=msg.timestamp.date().isoformat(),date_to=msg.timestamp.date().isoformat())
    assert preview['messages'] and 'hello attachment' in preview['messages'][0]['excerpt']
