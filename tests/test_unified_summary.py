from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import json
import runpy
from pathlib import Path

from qq_digest.manual_summary import ManualSummaryRequest, ManualSummaryService
from qq_digest.pipeline import DailyPipeline
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.report_revisions import ReportRevisionService
manual_helpers=runpy.run_path(str(Path(__file__).with_name('test_manual_summary.py')))
reading_helpers=runpy.run_path(str(Path(__file__).with_name('test_summary_reading.py')))
make_service,add_message=manual_helpers['make_service'],manual_helpers['add_message']
reading_client,login=reading_helpers['reading_client'],reading_helpers['login']


TZ = ZoneInfo('Asia/Shanghai')
DAY = date(2026, 9, 2)
REQUEST = ManualSummaryRequest((123,), DAY, DAY)


def unified(tmp_path):
    service, archive, ai = make_service(tmp_path, [GroupConfig(group_id=123,name='群一')])
    service.single_day_as_daily = True
    add_message(archive, 123, 'm1')
    return service, archive, ai


def daily(service, *, mode='today'):
    class Collector:
        def collect(self, group_id, start, end):
            return service.archive.messages_between(group_id,start,end)
    return DailyPipeline(archive=service.archive,collector=Collector(),
        ai_client=service.summarizer.ai,report_dir=service.report_writer.output_dir,
        max_context_chars=service.max_context_chars,timezone_name='Asia/Shanghai',
        knowledge_paths={},window_mode=mode)


def test_single_day_manual_then_automatic_reuses_one_report(tmp_path):
    service, archive, ai = unified(tmp_path)
    first = service.run(REQUEST)
    assert first.created_reports[0].report_key.startswith('daily:')
    daily(service).run_daily(datetime(2026,9,2,20,tzinfo=TZ))
    assert ai.calls == [123]
    assert archive.connection.execute('SELECT COUNT(*) FROM reports').fetchone()[0] == 1
    assert archive.connection.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0] == 0


def test_automatic_then_manual_reuses_same_identity_and_keeps_cutoff(tmp_path):
    service, archive, ai = unified(tmp_path)
    daily(service).run_daily(datetime(2026,9,2,20,tzinfo=TZ))
    row = archive.report_for(123,'2026-09-02')
    result = service.run(REQUEST)
    assert result.created_reports == []
    assert result.reused_reports[0].report_key == f"daily:{row['report_id']}"
    assert ai.calls == [123]


def test_changed_archive_updates_single_day_with_history_and_excludes_midnight(tmp_path):
    service, archive, ai = unified(tmp_path)
    first = service.run(REQUEST).created_reports[0]
    add_message(archive,123,'m-new',hour=11)
    stamp=datetime(2026,9,3,tzinfo=TZ)
    archive.ingest([NormalizedMessage(msg_id='next-day',group_id=123,sender_qq=1,
        text='不属于当天',timestamp=stamp,collected_at=stamp)])
    second=service.run(REQUEST).created_reports[0]
    assert second.report_key == first.report_key
    row=archive.report_for(123,'2026-09-02')
    assert row['source_message_count'] == 2
    versions=ReportRevisionService(archive).versions('daily',second.report_id)
    assert versions['total']==2
    assert ReportRevisionService(archive).version('daily',second.report_id,2)['payload']['window_end_inclusive'] is False
    assert 'next-day' not in ai.prompts[-1]
    assert service.run(REQUEST).reused_reports[0].report_key == first.report_key
    assert ai.calls == [123,123]


@pytest.mark.parametrize('window',['misaligned','legacy'])
def test_misaligned_window_and_legacy_window_are_not_reused_as_natural_day(tmp_path,window):
    service, archive, ai = unified(tmp_path)
    daily(service).run_daily(datetime(2026,9,2,20,tzinfo=TZ))
    row=archive.report_for(123,'2026-09-02')
    path=Path(row['json_path']);payload=json.loads(path.read_text(encoding='utf-8'))
    if window=='misaligned':payload['window_start']='2026-09-01T12:00:00+08:00'
    else:payload.pop('window_start');payload.pop('window_end')
    path.write_text(json.dumps(payload),encoding='utf-8')
    result=service.run(REQUEST)
    assert len(result.created_reports)==1
    assert result.created_reports[0].report_key.startswith('daily:')
    assert ai.calls==[123,123]


def test_unified_api_requires_login_and_validates_dates(reading_client):
    client,_,_=reading_client
    payload={'group_ids':[11],'start_date':'2026-09-01','end_date':'2026-09-08'}
    assert client.post('/api/summaries',json=payload).status_code==401
    login(client)
    assert client.post('/api/summaries',json=payload).status_code==422
    payload['group_ids']=[True]
    assert client.post('/api/summaries',json=payload).status_code==422


def test_unified_api_reuses_real_service_without_collection(reading_client,monkeypatch):
    RecordingAI=manual_helpers['RecordingAI']
    client,archive,config=reading_client
    ai=RecordingAI();ai.close=lambda:None
    monkeypatch.setattr('qq_digest.ai.factory.build_ai_client',lambda cfg:ai)
    add_message(archive,11,'m3')
    # The fixture timezone is UTC+14, so select the synthetic message's local day.
    config.summary.timezone='Asia/Shanghai'
    login(client)
    payload={'group_ids':[11],'start_date':'2026-09-02','end_date':'2026-09-02'}
    first=client.post('/api/summaries',json=payload)
    assert first.status_code==200,first.text
    assert first.json()['created_reports'][0]['report_key'].startswith('daily:')
    second=client.post('/api/summaries',json=payload).json()
    assert second['reused_reports'][0]['report_key']==first.json()['created_reports'][0]['report_key']
    assert ai.calls==[11]


def test_unified_generation_conflicts_with_running_daily(reading_client):
    client,_,_=reading_client
    login(client)
    payload={'group_ids':[11],'start_date':'2026-09-02','end_date':'2026-09-02'}
    with client.app.state.operations.claim('daily'):
        response=client.post('/api/summaries',json=payload)
    assert response.status_code==409
    assert response.json()['detail']['active']==['daily']
