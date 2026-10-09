"""Batch scope, atomicity and worker safety with synthetic local data only."""
import importlib
import importlib.util
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.history_inspection import HistoryInspectionService
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.operations import OperationBusy, OperationCoordinator


NOW = datetime(2026, 10, 9, 12, tzinfo=ZoneInfo('Asia/Shanghai'))


@pytest.fixture
def setup(tmp_path):
    ar = Archive.open(tmp_path / 'archive.sqlite')
    ar.upsert_groups([GroupConfig(group_id=123, name='first', read_files=True),
                      GroupConfig(group_id=456, name='second', read_files=True),
                      GroupConfig(group_id=789, name='disabled', enabled=False)])
    cfg = SimpleNamespace(archive_path=tmp_path / 'archive.sqlite', data_dir=tmp_path,
        work_dir=tmp_path / 'work', summary=SimpleNamespace(timezone='Asia/Shanghai'),
        ntqq=SimpleNamespace(enabled=True, db_dir='synthetic', qq_number=1, timezone='Asia/Shanghai'))
    yield ar, cfg, OperationCoordinator()
    ar.close()


def client_for(setup):
    name = 'qq_digest.web.group_batch_routes'
    assert importlib.util.find_spec(name) is not None, 'batch HTTP routes are missing'
    ar, cfg, operations = setup
    app = FastAPI()
    def login(request: Request):
        return None
    importlib.import_module(name).add_group_batch_routes(app, archive=lambda request: ar,
        config=lambda request: cfg, require_login=login, operations=operations)
    return TestClient(app)


def msg(gid, key='m1', **kwargs):
    return NormalizedMessage(group_id=gid, msg_id=key, timestamp=NOW-timedelta(days=1),
        collected_at=NOW, text='synthetic', **kwargs)


def payload(**kwargs):
    return {'group_ids': [123,456], 'start': '2026-10-01', 'end': '2026-10-09',
            'refresh': True, **kwargs}


def mock_source(monkeypatch, messages, failed=(), refresh_success=True):
    calls = {'refresh': 0, 'groups': []}
    def refresh(**kwargs):
        calls['refresh'] += 1
        return SimpleNamespace(success=refresh_success, message='synthetic refresh failure')
    class Source:
        def __init__(self, **kwargs): pass
        def collect(self, gid, start, end):
            calls['groups'].append(gid)
            if gid in failed:
                yield msg(gid, 'partial')
                raise RuntimeError('synthetic source error')
            yield from [m for m in messages if m.group_id == gid]
    monkeypatch.setattr('qq_digest.refresh.refresh_database', refresh)
    monkeypatch.setattr('qq_digest.group_batch.NTQQCollector', Source)
    return calls


def test_partial_inspection_preserves_unselected_snapshot(setup):
    ar, _, _ = setup
    inspector = HistoryInspectionService(ar)
    class Source:
        calls = []
        def collect(self, gid, start, end):
            self.calls.append(gid)
            return [msg(gid)]
    inspector.run(Source(), now=NOW)
    old = dict(ar.connection.execute('SELECT * FROM history_inspection_groups WHERE group_id=456').fetchone())
    source = Source(); source.calls = []
    result = inspector.run(source, now=NOW, group_ids=[123])
    assert result['groups_checked'] == 1 and source.calls == [123]
    assert dict(ar.connection.execute('SELECT * FROM history_inspection_groups WHERE group_id=456').fetchone()) == old


def test_collect_refreshes_once_and_isolates_failed_group(setup, monkeypatch):
    client = client_for(setup)
    calls = mock_source(monkeypatch, [msg(456), msg(456)], failed=[123])
    result = client.post('/api/collect/batch', json=payload()).json()
    assert calls == {'refresh': 1, 'groups': [123,456]}
    assert [r['status'] for r in result['results']] == ['failed','success']
    assert result['results'][1]['inserted'] == 1
    assert setup[0].count_messages(123) == 0 and setup[0].count_messages(456) == 1
    assert result['totals']['inserted'] == 1


def test_collect_refresh_failure_never_reads_stale_source(setup, monkeypatch):
    client = client_for(setup)
    calls = mock_source(monkeypatch, [msg(123)], refresh_success=False)
    result = client.post('/api/collect/batch', json=payload()).json()
    assert calls == {'refresh': 1, 'groups': []}
    assert all(r['status'] == 'failed' for r in result['results'])
    assert setup[0].count_messages(123) == 0


def test_collect_unknown_group_preflights_before_refresh(setup, monkeypatch):
    client = client_for(setup)
    calls = mock_source(monkeypatch, [msg(123)])
    assert client.post('/api/collect/batch', json=payload(group_ids=[123,999])).status_code == 404
    assert calls == {'refresh': 0, 'groups': []}
    assert setup[0].count_messages(123) == 0


def test_collect_rolls_back_group_if_success_state_write_fails(setup, monkeypatch):
    client = client_for(setup)
    mock_source(monkeypatch, [msg(123), msg(456)])
    original = Archive.mark_manual_collect_success
    def fail(ar, *, group_id, **kwargs):
        if group_id == 123: raise RuntimeError('synthetic state write failure')
        return original(ar, group_id=group_id, **kwargs)
    monkeypatch.setattr(Archive, 'mark_manual_collect_success', fail)
    result = client.post('/api/collect/batch', json=payload()).json()
    assert [r['status'] for r in result['results']] == ['failed','success']
    assert setup[0].count_messages(123) == 0 and setup[0].count_messages(456) == 1


@pytest.mark.parametrize('overrides', [{'group_ids': []}, {'group_ids':[True]},
    {'group_ids':['123']}, {'group_ids':None}, {'start':'2026-1-01'},
    {'start':'2026-10-01T00:00:00'}, {'end':'2026-09-01'}, {'dry_run':'false'},
    {'refresh':'true'}, {'extra':1}, {'start':123}])
def test_collect_rejects_invalid_bounds_and_schema(setup, overrides):
    response = client_for(setup).post('/api/collect/batch', json=payload(**overrides))
    assert response.status_code in (400,422)


def test_dry_run_checks_gaps_without_changing_messages_or_sync(setup, monkeypatch):
    client = client_for(setup)
    mock_source(monkeypatch, [msg(123), msg(123), msg(456)])
    result = client.post('/api/collect/batch', json=payload(dry_run=True)).json()
    assert result['results'][0]['missing_days'] == [{'date':'2026-10-08','count':1}]
    assert result['totals']['inserted'] == 0
    assert setup[0].connection.execute('SELECT COUNT(*) FROM sync_state').fetchone()[0] == 0


def test_settings_only_updates_selected_explicit_fields_and_preflights(setup):
    client = client_for(setup)
    assert client.post('/api/groups/batch/settings', json={'group_ids':[123,999],
        'patch':{'read_files':False}}).status_code == 404
    assert next(g for g in setup[0].all_groups() if g.group_id==123).read_files is True
    response = client.post('/api/groups/batch/settings', json={'group_ids':[123], 'patch':{'category':'study'}})
    assert response.status_code == 200
    groups = {g.group_id:g for g in setup[0].all_groups()}
    assert groups[123].category == 'study' and groups[123].read_files is True
    assert groups[456].category == 'general'


@pytest.mark.parametrize('patch', [{}, {'read_files':'false'}, {'enabled':1},
    {'collection_window_days':True}, {'keywords':['']}, {'category':'invalid'}, {'name':'new'}])
def test_settings_rejects_invalid_patch(setup, patch):
    assert client_for(setup).post('/api/groups/batch/settings', json={'group_ids':[123],
        'patch':patch}).status_code in (400,422)


def test_add_skips_existing_without_overwriting_settings(setup):
    result = client_for(setup).post('/api/groups/batch/add', json={'groups':[
        {'group_id':123,'name':'changed'}, {'group_id':222,'name':'new'}]})
    assert result.status_code == 200
    groups = {g.group_id:g for g in setup[0].all_groups()}
    assert groups[123].name == 'first' and groups[123].read_files is True
    assert groups[222].name == 'new'
    assert [r['status'] for r in result.json()['results']] == ['skipped','success']


def test_attachment_batch_preserves_global_twenty_limit_and_missing_status(setup, monkeypatch):
    client = client_for(setup)
    messages = [msg(gid, f'file{i}', message_type='file', content_json={'attachments':[
        {'key':'a','kind':'file','name':'synthetic.txt','md5':'a'*32,'path':''}]})
        for gid in (123,456) for i in range(15)]
    setup[0].ingest(messages)
    monkeypatch.setattr('qq_digest.attachments.AttachmentService._hydrate', lambda self,m,g:m)
    result = client.post('/api/groups/batch/attachments/read', json={'group_ids':[123,456]})
    assert result.status_code == 200
    rows = setup[0].connection.execute('SELECT * FROM message_attachments').fetchall()
    assert len(rows) == 20 and all(row['status']=='missing' for row in rows)
    assert {row['group_id'] for row in rows} == {123,456}


def test_attachment_preflight_unknown_group_prevents_any_reads(setup, monkeypatch):
    client = client_for(setup)
    calls = []
    monkeypatch.setattr('qq_digest.attachments.AttachmentService.prepare', lambda *a,**k:calls.append(1))
    assert client.post('/api/groups/batch/attachments/read', json={'group_ids':[123,999]}).status_code == 404
    assert calls == []


def test_batch_honors_conflicts_and_storage_handoff_barrier(setup):
    client = client_for(setup)
    with setup[2].claim('backup'):
        assert client.post('/api/groups/batch/settings', json={'group_ids':[123],
            'patch':{'read_files':False}}).status_code == 409
    setup[2].blocker = lambda: 'storage handoff pending'
    assert client.post('/api/groups/batch/add', json={'groups':[{'group_id':222,'name':'new'}]}).status_code == 409


def test_collection_preserves_automatic_attachment_processing_in_one_call(setup, monkeypatch):
    client = client_for(setup)
    mock_source(monkeypatch,[msg(123),msg(456)])
    calls=[]
    monkeypatch.setattr('qq_digest.attachments.AttachmentService.prepare',
        lambda self,messages,**kwargs:calls.append([m.group_id for m in messages]))
    assert client.post('/api/collect/batch',json=payload()).status_code == 200
    assert calls == [[123,456]]


def test_inspection_snapshot_filters_before_counting_and_paging(setup):
    result = HistoryInspectionService(setup[0]).snapshot(group_ids=[456],page_size=1)
    assert result['total']==1 and [g['group_id'] for g in result['groups']]==[456]


@pytest.mark.parametrize('ids',[[],[True],[123,999]])
def test_inspection_preflight_does_not_start_job_or_prepare(setup, ids):
    calls=[]
    with pytest.raises((ValueError,KeyError)):
        HistoryInspectionService(setup[0]).run(None,now=NOW,group_ids=ids,prepare=lambda:calls.append(1))
    assert calls==[] and setup[0].connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0


def test_cancelled_batch_request_keeps_claim_until_worker_finishes(setup, monkeypatch):
    import asyncio
    import threading
    from starlette.requests import Request
    from qq_digest.web.group_batch_routes import CollectBatchRequest
    app = client_for(setup).app
    started=threading.Event(); finish=threading.Event()
    def blocked(**kwargs):
        started.set(); assert finish.wait(5)
        return {'results':[],'totals':{}}
    monkeypatch.setattr('qq_digest.web.group_batch_routes.collect_batch',blocked)
    endpoint = next(route.endpoint for route in app.routes if getattr(route,'path','')=='/api/collect/batch')
    async def exercise():
        task=asyncio.create_task(endpoint(Request({'type':'http','app':app}),CollectBatchRequest(**payload())))
        try:
            assert await asyncio.to_thread(started.wait,5)
            task.cancel()
            await asyncio.sleep(0.01)
            assert setup[2].is_active('collect') and not task.done()
            with pytest.raises(OperationBusy):
                with setup[2].claim('storage_mutation'): pass
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert not setup[2].is_active('collect')
    asyncio.run(exercise())


def test_partial_inspection_does_not_postpone_due_full_inspection(setup):
    ar,_,_ = setup
    inspector = HistoryInspectionService(ar)
    class EmptySource:
        def collect(self,*args): return []
    full = inspector.run(EmptySource(),now=NOW)
    with ar.transaction():
        ar.connection.execute('UPDATE jobs SET finished_at=? WHERE job_id=?',
            ((NOW-timedelta(hours=25)).isoformat(),full['job_id']))
    assert inspector.due(NOW) is True
    partial = inspector.run(EmptySource(),now=NOW,group_ids=[123])
    with ar.transaction():
        ar.connection.execute('UPDATE jobs SET finished_at=? WHERE job_id=?',(NOW.isoformat(),partial['job_id']))
    assert inspector.due(NOW) is True
    latest = inspector.snapshot()['latest_run']
    assert latest['job_id']==partial['job_id'] and latest['scope']=='selected'
    assert latest['group_ids']==[123]


def test_partial_inspection_before_first_full_run_leaves_full_inspection_due(setup):
    inspector = HistoryInspectionService(setup[0])
    class EmptySource:
        def collect(self,*args): return []
    inspector.run(EmptySource(),now=NOW,group_ids=[123])
    assert inspector.due(NOW) is True


def test_failed_partial_refresh_records_scope_without_changing_full_schedule(setup):
    inspector = HistoryInspectionService(setup[0])
    def fail(): raise RuntimeError('synthetic refresh error')
    result = inspector.run(None,now=NOW,group_ids=[123,789],prepare=fail)
    assert result['status']=='failed' and result['groups_checked']==0
    latest = inspector.snapshot()['latest_run']
    assert latest['scope']=='selected' and latest['group_ids']==[123,789]
    assert inspector.due(NOW) is True


def test_scheduled_inspection_after_partial_still_checks_all_enabled_groups(setup, monkeypatch):
    import asyncio
    from qq_digest.web.history_inspection import run_scheduled_inspection
    ar,cfg,operations = setup
    inspector = HistoryInspectionService(ar)
    class EmptySource:
        def __init__(self): self.calls=[]
        def collect(self,gid,*args):
            self.calls.append(gid)
            return []
    inspector.run(EmptySource(),now=NOW,group_ids=[123])
    source = EmptySource()
    def scheduled(**kwargs):
        assert 'group_ids' not in kwargs
        inspector.run(source,now=NOW)
    monkeypatch.setattr('qq_digest.web.history_inspection.run_history_inspection',scheduled)
    assert asyncio.run(run_scheduled_inspection(cfg,operations)) is True
    assert source.calls==[123,456]
    assert inspector.snapshot()['latest_run']['scope']=='all'
