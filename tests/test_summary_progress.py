from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from uuid import uuid4

import runpy
from pathlib import Path
helpers = runpy.run_path(str(Path(__file__).with_name('test_unified_summary.py')))
reading_client, login = helpers['reading_client'], helpers['login']
manual_helpers, add_message = helpers['manual_helpers'], helpers['add_message']
from qq_digest.manual_summary import ManualSummaryRequest
from qq_digest.models import GroupConfig


def test_service_reports_real_stages_and_group_outcomes(tmp_path):
    groups = [GroupConfig(group_id=n, name=f'群{n}') for n in (11, 22, 33)]
    service, archive, ai = manual_helpers['make_service'](tmp_path, groups)
    add_message(archive, 11, 'm11')
    add_message(archive, 33, 'm33')
    request = ManualSummaryRequest([11, 22, 33], date(2026, 9, 2), date(2026, 9, 2))
    events = []
    service.run(request, on_progress=lambda **event: events.append(event))
    assert [e['stage'] for e in events if e.get('group_id') == 11] == ['scanning', 'summary', 'publication', 'created']
    assert [e['stage'] for e in events if e.get('group_id') == 22] == ['scanning', 'skipped']
    events.clear()
    service.run(request, on_progress=lambda **event: events.append(event))
    assert [e['stage'] for e in events if e.get('group_id') == 11] == ['scanning', 'reused']


def test_generation_progress_visible_during_ai_and_recovers_result(reading_client, monkeypatch):
    client, archive, config = reading_client
    config.summary.timezone = 'Asia/Shanghai'
    add_message(archive, 11, 'p11')
    ai = manual_helpers['RecordingAI']()
    entered, release = Event(), Event()
    original = ai.chat
    def slow_chat(messages):
        entered.set()
        assert release.wait(10)
        return original(messages)
    ai.chat = slow_chat
    ai.close = lambda: None
    monkeypatch.setattr('qq_digest.ai.factory.build_ai_client', lambda _: ai)
    progress_url = '/api/summaries/progress'
    assert client.get(progress_url).status_code == 401
    login(client)
    generation_id = str(uuid4())
    payload = dict(group_ids=[11], start_date='2026-09-02', end_date='2026-09-02', generation_id=generation_id)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(client.post, '/api/summaries', json=payload)
        try:
            assert entered.wait(5)
            progress = client.get(progress_url).json()['generation']
            assert progress['generation_id'] == generation_id
            assert progress['status'] == 'running'
            assert progress['stage'] == 'summary'
            assert progress['completed'] == 0 and progress['total'] == 1
            assert progress['current_group']['group_id'] == 11
            assert client.post('/api/summaries', json=payload).status_code == 409
        finally:
            release.set()
        response = pending.result()
    assert response.status_code == 200, response.text
    progress = client.get(progress_url, params={'generation_id': generation_id}).json()['generation']
    assert progress['status'] == 'success'
    assert progress['completed'] == 1 and progress['counts']['created'] == 1
    assert progress['result'] == response.json()
    assert client.post('/api/summaries', json=payload).status_code == 409
    assert ai.calls == [11]


def test_progress_counts_partial_failure_and_does_not_fake_completion():
    from qq_digest.summary_progress import SummaryProgress
    store = SummaryProgress()
    identifier = str(uuid4())
    store.start(identifier, dict(group_ids=[11, 22], start_date='2026-10-05', end_date='2026-10-05'))
    store.update(identifier, group_id=11, group_name='群11', stage='created')
    store.update(identifier, group_id=22, group_name='群22', stage='failed')
    store.finish(identifier, result={'status': 'partial_success'})
    snapshot = store.snapshot(identifier)
    assert snapshot['completed'] == 2
    assert snapshot['counts']['created'] == snapshot['counts']['failed'] == 1
    assert snapshot['status'] == 'partial_success'
    other = str(uuid4())
    store.start(other, dict(group_ids=[11, 22], start_date='2026-10-05', end_date='2026-10-05'))
    store.finish(other, error='连接失败')
    assert store.snapshot(other)['completed'] == 0
    assert store.snapshot(other)['status'] == 'failed'
    # Returned objects must not mutate the worker's state.
    snapshot['counts']['created'] = 99
    assert store.snapshot(identifier)['counts']['created'] == 1


def test_disconnected_request_keeps_operation_owned_by_worker():
    import asyncio
    from fastapi import FastAPI
    from qq_digest.web.operations import OperationCoordinator
    from qq_digest.web.summary_generation_routes import add_summary_generation_routes, GenerationPayload
    entered, release = Event(), Event()
    coordinator = OperationCoordinator()
    result = dict(status='success', created_reports=[], reused_reports=[], skipped_groups=[], failed_groups=[])
    def worker(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return result
    app = FastAPI()
    add_summary_generation_routes(app, archive=None, config=object(), require_login=lambda: None,
                                 run_summary=worker, operations=coordinator)
    endpoint = next(r.endpoint for r in app.routes if r.path == '/api/summaries')
    async def scenario():
        task = asyncio.create_task(endpoint(GenerationPayload(group_ids=[11], start_date='2026-10-05', end_date='2026-10-05')))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            assert coordinator.is_active('manual_summary'), 'disconnect must not unlock a still-running AI worker'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            for _ in range(50):
                if not coordinator.is_active('manual_summary'):
                    break
                await asyncio.sleep(0.01)
        assert not coordinator.is_active('manual_summary')
    asyncio.run(scenario())
