import pytest
from tests.test_web import web_client


def enable(client):
    client.post('/login', data={'password': 'password123'})
    state = client.get('/api/features').json()
    return client.put('/api/features', json={'values': {'reminders': True}, 'expected_revision': state['revision']})


def rule(**changes):
    return dict(name='教程提醒', kind='keyword', enabled=True, group_ids=[123],
                keywords=['教程'], channels=['in_app'], quiet_start='00:00', quiet_end='00:00', **changes)


def test_reminder_auth_and_opt_in(web_client):
    client, _, _ = web_client
    assert client.get('/api/reminder-rules').status_code == 401
    client.post('/login', data={'password': 'password123'})
    assert client.get('/api/reminders').status_code == 403
    assert client.get('/reminders', follow_redirects=False).status_code == 303
    assert enable(client).status_code == 200
    assert client.get('/reminders').status_code == 200
    assert client.get('/api/reminders/capabilities').json()['windows'] is False


def test_reminder_rule_crud_preview_and_conflict(web_client):
    client, _, _ = web_client
    assert enable(client).status_code == 200
    preview = client.post('/api/reminder-rules/preview', json=rule())
    assert preview.status_code == 200
    assert client.get('/api/reminder-rules').json()['rules'] == []
    response = client.post('/api/reminder-rules', json=rule())
    assert response.status_code == 200, response.text
    saved = response.json()
    url = f"/api/reminder-rules/{saved['rule_id']}"
    changed = client.patch(url, json=dict(rule(), name='更名', expected_revision=saved['revision']))
    assert changed.status_code == 200
    assert client.patch(url, json=dict(rule(), expected_revision=saved['revision'])).status_code == 409
    assert client.delete(url, params={'expected_revision': changed.json()['revision']}).status_code == 200
    assert client.get('/api/reminder-rules').json()['rules'] == []


@pytest.mark.parametrize('changes', [{'enabled': 'false'}, {'group_ids': [999]}, {'keywords': []}, {'channels': ['qq']}, {'quiet_start': '25:00'}, {'extra': 1}])
def test_reminder_invalid_input(web_client, changes):
    client, _, _ = web_client
    enable(client)
    assert client.post('/api/reminder-rules', json=dict(rule(), **changes)).status_code == 422


def test_reminder_worker_conflicts_and_independent_commit(web_client):
    client, _, _ = web_client
    enable(client)
    with client.app.state.operations.claim('backup'):
        assert client.post('/api/reminder-rules', json=rule()).status_code == 409
    assert client.post('/api/reminder-rules', json=rule()).status_code == 200
    client.app.state.archive.connection.rollback()
    assert len(client.get('/api/reminder-rules').json()['rules']) == 1
    assert client.post('/api/reminders/check').status_code == 200
    assert client.get('/api/reminders?page=0').status_code == 422


def test_disabled_scheduler_never_touches_database():
    import asyncio
    from qq_digest.reminder_scheduler import run_reminder_check
    assert asyncio.run(run_reminder_check(object(), object(), enabled=lambda: False)) is False


def test_failed_reminder_worker_is_not_committed_by_shared_connection(web_client, monkeypatch):
    from threading import Event, Thread
    from qq_digest.reminders import ReminderService
    client, _, _ = web_client
    enable(client)
    entered, release = Event(), Event()
    original = ReminderService.save_rule

    def failing(service, values, **kwargs):
        service.connection.execute("INSERT INTO reminder_rules(values_json,failure_baseline) VALUES(?,'x')", ('{}',))
        entered.set()
        assert release.wait(5)
        raise OSError('synthetic worker failure')

    monkeypatch.setattr(ReminderService, 'save_rule', failing)
    responses=[]
    thread=Thread(target=lambda: responses.append(client.post('/api/reminder-rules', json=rule())))
    thread.start()
    try:
        assert entered.wait(5)
        client.app.state.archive.connection.commit()
        assert client.post('/api/reminders/check').status_code == 409
    finally:
        release.set()
        thread.join(10)
    assert responses[0].status_code == 503
    monkeypatch.setattr(ReminderService, 'save_rule', original)
    assert client.get('/api/reminder-rules').json()['rules'] == []


def test_parallel_reminder_reads_do_not_conflict_with_each_other(web_client, monkeypatch):
    from threading import Event, Thread
    from qq_digest.reminders import ReminderService
    client, _, _ = web_client
    enable(client)
    entered, release, second_done=Event(),Event(),Event()
    original=ReminderService.list_rules
    def slow_rules(service):
        entered.set()
        assert release.wait(5)
        return original(service)
    monkeypatch.setattr(ReminderService,'list_rules',slow_rules)
    responses=[]
    first=Thread(target=lambda:responses.append(client.get('/api/reminder-rules')))
    def load_second():
        responses.append(client.get('/api/reminders'))
        second_done.set()
    second=Thread(target=load_second)
    first.start()
    assert entered.wait(5)
    second.start()
    second_done.wait(.1)
    release.set()
    first.join(10)
    second.join(10)
    assert len(responses)==2 and all(response.status_code==200 for response in responses)


def test_feature_disable_cannot_overtake_active_reminder_check(web_client, monkeypatch):
    from threading import Event, Thread
    from qq_digest.reminders import ReminderService
    client, _, _=web_client
    enable(client)
    entered,release=Event(),Event()
    original=ReminderService.scan
    def slow(service):
        entered.set()
        assert release.wait(5)
        return original(service)
    monkeypatch.setattr(ReminderService,'scan',slow)
    responses=[]
    thread=Thread(target=lambda:responses.append(client.post('/api/reminders/check')))
    thread.start()
    try:
        assert entered.wait(5)
        state=client.get('/api/features').json()
        update=client.put('/api/features',json={'values':{'reminders':False},'expected_revision':state['revision']})
        assert update.status_code==409
        assert client.app.state.features.enabled('reminders') is True
    finally:
        release.set()
        thread.join(10)
    assert responses[0].status_code==200
    assert client.put('/api/features',json={'values':{'reminders':False},'expected_revision':state['revision']}).status_code==200
    assert client.post('/api/reminders/check').status_code==403


def test_canceled_request_retains_lock_until_worker_finishes(web_client, monkeypatch):
    import asyncio
    import httpx
    from threading import Event
    from qq_digest.reminders import ReminderService
    from qq_digest.operations import OperationBusy
    client, _, _=web_client
    enable(client)
    entered,release=Event(),Event()
    original=ReminderService.scan
    def slow(service):
        entered.set()
        assert release.wait(5)
        return original(service)
    monkeypatch.setattr(ReminderService,'scan',slow)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),base_url='http://testserver',cookies=client.cookies) as http:
            request=asyncio.create_task(http.post('/api/reminders/check'))
            try:
                assert await asyncio.to_thread(entered.wait,5)
                request.cancel()
                await asyncio.gather(request,return_exceptions=True)
                assert client.app.state.operations.is_active('reminder_mutation')
                with pytest.raises(OperationBusy):
                    with client.app.state.operations.claim('backup'):
                        pass
            finally:
                release.set()
            for _ in range(100):
                if not client.app.state.operations.is_active('reminder_mutation'):
                    break
                await asyncio.sleep(.01)
            assert client.app.state.operations.can_start('backup')
    asyncio.run(scenario())
