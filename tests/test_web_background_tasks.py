import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
import threading
from zoneinfo import ZoneInfo

import pytest

from qq_digest.archive import Archive
from qq_digest.config import AIConfig, Config, SecurityConfig, SummaryConfig
from qq_digest.features import FeatureService
from qq_digest.models import GroupConfig
from qq_digest.operations import OperationCoordinator


NOW = datetime(2026, 10, 12, 12, tzinfo=ZoneInfo('Asia/Shanghai'))


def save_job(archive, target, status='success', finished=None):
    finished = finished or NOW - timedelta(hours=1)
    with archive.transaction():
        archive.connection.execute(
            """INSERT INTO jobs(job_type,status,started_at,finished_at,target_date)
               VALUES('daily_digest',?,?,?,?)""",
            (status, finished.isoformat(), finished.isoformat(), target),
        )


@pytest.fixture
def runtime(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    archive.upsert_groups([GroupConfig(group_id=123, name='合成群')])
    config = Config(
        data_dir=tmp_path, archive_path=tmp_path / 'archive.sqlite',
        report_dir=tmp_path / 'reports', knowledge_dir=tmp_path / 'knowledge',
        work_dir=tmp_path / 'work', log_dir=tmp_path / 'logs',
        security=SecurityConfig(web_password_hash='x' * 32),
        ai=AIConfig(base_url='https://synthetic.invalid', model='test', api_key_env='TEST_UNUSED'),
        summary=SummaryConfig(hour=9, minute=30, timezone='Asia/Shanghai', window_mode='today'),
    )
    config.ntqq.enabled = True
    config.ntqq.db_dir = 'synthetic-unused'
    for days in range(1, 4):
        save_job(archive, (NOW.date() - timedelta(days=days)).isoformat())
    context = SimpleNamespace(
        archive=archive, config=config, features=FeatureService(archive),
        operations=OperationCoordinator(),
        daily={'running': False, 'task': None, 'last_run_date': '',
               'last_attempt_date': '', 'last_result': None},
        sync={'running': False, 'task': None, 'last_run_at': '', 'last_result': None},
    )
    try:
        yield context
    finally:
        archive.close()


def load_background(monkeypatch):
    from qq_digest.web import background_tasks

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(background_tasks, 'datetime', FixedDateTime)
    return background_tasks


class StopLoop(Exception):
    pass


def run_cycles(monkeypatch, background, loop, *, cycles=1, before_tick=None):
    original_sleep = asyncio.sleep
    ticks = []

    async def tick(delay):
        ticks.append(delay)
        if len(ticks) > cycles:
            raise StopLoop()
        if before_tick:
            before_tick(len(ticks))
        await original_sleep(0)

    monkeypatch.setattr(background.asyncio, 'sleep', tick)
    with pytest.raises(StopLoop):
        asyncio.run(loop())
    return ticks


def daily_loop(background, runtime, worker):
    return background.daily_scheduler_loop(
        runtime.config, runtime.archive, runtime.operations,
        runtime.features, runtime.daily, worker,
    )


@pytest.mark.parametrize('status,minutes_ago,attempts,due', [
    ('failed', 30, 1, True), ('partial_success', 30, 1, True),
    ('failed', 1, 1, False), ('failed', 30, 3, False),
    ('success', 30, 1, False),
])
def test_daily_uses_persisted_retry_budget(runtime, monkeypatch, status, minutes_ago, attempts, due):
    background = load_background(monkeypatch)
    for _ in range(attempts):
        save_job(runtime.archive, NOW.date().isoformat(), status,
                 NOW - timedelta(minutes=minutes_ago))
    result = {'success': True, 'status': 'success'}
    calls = []

    def worker(config):
        assert config is runtime.config
        assert runtime.daily['running'] and runtime.operations.is_active('daily')
        calls.append(config)
        return result

    ticks = run_cycles(monkeypatch, background, lambda: daily_loop(background, runtime, worker))
    assert ticks == [60, 60]
    assert bool(calls) is due
    assert not runtime.daily['running'] and not runtime.operations.is_active('daily')
    assert runtime.daily['last_run_date'] == (NOW.date().isoformat() if due else '')
    assert runtime.daily['last_result'] == (result if due else None)


@pytest.mark.parametrize('newer_job', [False, True])
def test_daily_catchup_preserves_latest_result(runtime, monkeypatch, newer_job):
    background = load_background(monkeypatch)
    missing = NOW.date() - timedelta(days=1)
    with runtime.archive.transaction():
        runtime.archive.connection.execute('DELETE FROM jobs WHERE target_date=?', (missing.isoformat(),))
    if newer_job:
        save_job(runtime.archive, NOW.date().isoformat(), 'failed')
    saved_result = {'status': 'failed', 'error': 'newer report'}
    runtime.daily['last_result'] = saved_result
    result = {'success': True, 'status': 'success'}
    calls = []

    def worker(config, run_at):
        assert runtime.operations.is_active('daily') and runtime.daily['running']
        calls.append(run_at)
        save_job(runtime.archive, missing.isoformat())
        return result

    run_cycles(monkeypatch, background, lambda: daily_loop(background, runtime, worker))
    assert calls == [datetime(2026, 10, 11, 23, 59, 59, 999999, tzinfo=NOW.tzinfo)]
    assert runtime.daily['last_attempt_date'] == missing.isoformat()
    assert runtime.daily['last_run_date'] == ''
    assert runtime.daily['last_result'] == (saved_result if newer_job else result)
    assert not runtime.daily['running']


@pytest.mark.parametrize('pause', ['feature', 'setup', 'running', 'busy', 'no_config'])
def test_daily_pauses_before_worker(runtime, monkeypatch, pause):
    background = load_background(monkeypatch)
    if pause == 'feature':
        runtime.features.update({'auto_daily': False}, 0)
    elif pause == 'setup':
        runtime.config.first_use = {'active': True}
    elif pause == 'running':
        runtime.daily['running'] = True
    elif pause == 'busy':
        runtime.operations.blocker = lambda: 'storage handoff'
    else:
        runtime.config = None

    def forbidden(*args):
        pytest.fail('paused scheduler entered daily work')

    run_cycles(monkeypatch, background, lambda: daily_loop(background, runtime, forbidden))
    assert runtime.daily['last_result'] is None
    assert runtime.daily['last_attempt_date'] == ''


def test_daily_reads_live_feature_switch(runtime, monkeypatch):
    background = load_background(monkeypatch)
    runtime.features.update({'auto_daily': False}, 0)
    calls = []

    def worker(config):
        calls.append(config)
        return {'success': False, 'status': 'partial_success'}

    def before_tick(index):
        if index == 2:
            runtime.features.update({'auto_daily': True}, 1)

    run_cycles(monkeypatch, background, lambda: daily_loop(background, runtime, worker),
               cycles=2, before_tick=before_tick)
    assert len(calls) == 1
    assert runtime.daily['last_result']['status'] == 'partial_success'
    assert runtime.daily['last_run_date'] == ''


def test_daily_handles_admission_race(runtime, monkeypatch):
    background = load_background(monkeypatch)
    monkeypatch.setattr(runtime.operations, 'can_start', lambda kind: True)

    def forbidden(*args):
        pytest.fail('claim conflict reached worker')

    with runtime.operations.claim('manual_summary'):
        run_cycles(monkeypatch, background, lambda: daily_loop(background, runtime, forbidden))
    assert not runtime.daily['running'] and runtime.daily['last_result'] is None


def test_sync_reads_live_switch_and_keeps_shared_result(runtime, monkeypatch):
    background = load_background(monkeypatch)
    runtime.features.update({'auto_collection': False}, 0)
    result = {'success': True, 'ingested': 3}
    calls = []

    def worker(*, config):
        assert runtime.sync['running'] and runtime.operations.is_active('sync')
        calls.append(config)
        return SimpleNamespace(to_dict=lambda: result)

    monkeypatch.setattr('qq_digest.sync.run_message_sync', worker)

    def before_tick(index):
        if index == 2:
            runtime.features.update({'auto_collection': True}, 1)

    ticks = run_cycles(monkeypatch, background, lambda: background.message_sync_loop(
        runtime.config, runtime.operations, runtime.features, runtime.sync),
        cycles=2, before_tick=before_tick)
    assert ticks == [runtime.config.collection.startup_delay_seconds, 600, 600]
    assert calls == [runtime.config]
    assert runtime.sync['last_result'] == result
    assert runtime.sync['last_run_at'] == '2026-10-12 12:00'
    assert not runtime.sync['running'] and not runtime.operations.is_active('sync')


@pytest.mark.parametrize('pause', ['collection', 'ntqq', 'setup', 'busy'])
def test_sync_pauses_before_worker(runtime, monkeypatch, pause):
    background = load_background(monkeypatch)
    if pause == 'collection':
        runtime.config.collection.enabled = False
    elif pause == 'ntqq':
        runtime.config.ntqq.enabled = False
    elif pause == 'setup':
        runtime.config.first_use = {'active': True}
    else:
        runtime.operations.blocker = lambda: 'storage handoff'

    def forbidden(**kwargs):
        pytest.fail('paused collector reached worker')

    monkeypatch.setattr('qq_digest.sync.run_message_sync', forbidden)
    run_cycles(monkeypatch, background, lambda: background.message_sync_loop(
        runtime.config, runtime.operations, runtime.features, runtime.sync))
    assert runtime.sync['last_result'] is None and not runtime.sync['running']


@pytest.mark.parametrize('kind', ['daily', 'sync'])
def test_worker_error_releases_running_state_and_claim(runtime, monkeypatch, kind):
    background = load_background(monkeypatch)

    def broken(*args, **kwargs):
        raise ValueError('synthetic worker failure')

    if kind == 'daily':
        loop = lambda: daily_loop(background, runtime, broken)
    else:
        monkeypatch.setattr('qq_digest.sync.run_message_sync', broken)
        loop = lambda: background.message_sync_loop(
            runtime.config, runtime.operations, runtime.features, runtime.sync)
    run_cycles(monkeypatch, background, loop)
    assert not getattr(runtime, kind)['running']
    assert not runtime.operations.is_active(kind)
    assert getattr(runtime, kind)['last_result']['success'] is False


@pytest.mark.parametrize('kind', ['daily', 'catchup', 'sync'])
def test_cancellation_waits_for_physical_worker(runtime, monkeypatch, kind):
    background = load_background(monkeypatch)
    original_sleep = asyncio.sleep
    started, release = threading.Event(), threading.Event()

    async def tick(delay):
        await original_sleep(0)

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return SimpleNamespace(to_dict=lambda: {}) if kind == 'sync' else {'success': True}

    monkeypatch.setattr(background.asyncio, 'sleep', tick)
    if kind == 'catchup':
        monkeypatch.setattr(background, 'pending_catchup_date',
                            lambda *args, **kwargs: NOW.date() - timedelta(days=1))
    if kind == 'sync':
        monkeypatch.setattr('qq_digest.sync.run_message_sync', blocked)
    state = runtime.sync if kind == 'sync' else runtime.daily
    operation = 'sync' if kind == 'sync' else 'daily'

    async def scenario():
        loop = daily_loop(background, runtime, blocked) if kind != 'sync' else background.message_sync_loop(
            runtime.config, runtime.operations, runtime.features, runtime.sync)
        task = asyncio.create_task(loop)
        try:
            for _ in range(1000):
                if started.is_set():
                    break
                await original_sleep(.001)
            assert started.is_set()
            task.cancel()
            await original_sleep(.01)
            task.cancel()
            await original_sleep(.01)
            assert not task.done()
            assert state['running'] and runtime.operations.is_active(operation)
        finally:
            release.set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not state['running'] and not runtime.operations.is_active(operation)
        assert state['last_result'] is None

    asyncio.run(scenario())


def test_notification_queue_retries_after_logged_error(runtime, monkeypatch, caplog):
    background = load_background(monkeypatch)
    notifier = object()
    calls = []

    async def process(config, operations, sink):
        assert config is runtime.config and operations is runtime.operations and sink is notifier
        calls.append(sink)
        if len(calls) == 1:
            raise ValueError('synthetic queue failure')

    monkeypatch.setattr('qq_digest.web.storage_handoff.run_notification_queue', process)
    ticks = run_cycles(monkeypatch, background, lambda: background.notification_queue_loop(
        runtime.config, runtime.operations, notifier))
    assert calls == [notifier, notifier] and ticks == [60, 60]
    assert '通知队列处理失败' in caplog.text


@pytest.mark.parametrize('bot', [False, True])
def test_lifespan_stops_and_awaits_all_tasks(runtime, monkeypatch, bot):
    background = load_background(monkeypatch)
    from qq_digest.candidates import CandidateService
    from qq_digest.knowledge import KnowledgeWriter
    from qq_digest.notify import QQBotNotifier, BotCommandHandler

    runtime.config.qq_bot.enabled = bot
    runtime.config.qq_bot.app_id = 'synthetic-bot' if bot else ''
    events = []
    tasks = []

    async def idle(name, **kwargs):
        events.append('start:' + name)
        tasks.append(asyncio.current_task())
        try:
            await asyncio.Event().wait()
        finally:
            events.append('stop:' + name)

    async def daily(config, archive, operations, features, state, worker):
        assert state is runtime.daily and features is runtime.features
        await idle('daily')

    async def sync(config, operations, features, state):
        assert state is runtime.sync and features is runtime.features
        await idle('sync')

    async def inspection(config, operations, *, enabled):
        assert enabled()
        await idle('inspection')

    sink = object()

    async def reminders(path, operations, *, enabled, timezone_name, sink_getter):
        assert path == str(runtime.config.archive_path)
        assert timezone_name == 'Asia/Shanghai' and sink_getter() is sink
        assert not enabled()  # Reminder feature is disabled by default.
        await idle('reminders')

    async def notifications(config, operations, notifier):
        assert isinstance(notifier, QQBotNotifier)
        await idle('notifications')

    class Listener:
        def __init__(self, *, notifier, handler, operations):
            assert isinstance(handler, BotCommandHandler)
            assert operations is runtime.operations

        async def start(self):
            await idle('bot')

        async def stop(self):
            events.append('listener-stop')

    def close_topics():
        assert all(task.done() for task in tasks)
        events.append('topics-close')

    monkeypatch.setattr(background, 'daily_scheduler_loop', daily)
    monkeypatch.setattr(background, 'message_sync_loop', sync)
    monkeypatch.setattr(background, 'history_inspection_loop', inspection)
    monkeypatch.setattr(background, 'reminder_loop', reminders)
    monkeypatch.setattr(background, 'notification_queue_loop', notifications)
    monkeypatch.setattr('qq_digest.notify.WebSocketListener', Listener)
    app = SimpleNamespace(state=SimpleNamespace(features=runtime.features,
        reminder_sink=sink, close_topic_tracking=close_topics))
    lifespan = background.background_lifespan(
        config=runtime.config, archive=runtime.archive,
        candidates=CandidateService(runtime.archive), knowledge=KnowledgeWriter(runtime.config.knowledge_dir),
        operations=runtime.operations, scheduler_state=runtime.daily,
        sync_scheduler_state=runtime.sync, run_daily_task=lambda cfg: None,
    )

    async def scenario():
        async with lifespan(app):
            await asyncio.sleep(0)
            assert len(tasks) == (6 if bot else 4)
            assert runtime.daily['task'] in tasks and runtime.sync['task'] in tasks
        assert all(task.done() for task in tasks)

    asyncio.run(scenario())
    starts = ['start:daily', 'start:sync', 'start:inspection', 'start:reminders']
    assert events[:len(starts) + (2 if bot else 0)] == (
        ['start:bot', 'start:notifications'] + starts if bot else starts)
    if bot:
        assert events.index('listener-stop') < min(events.index('stop:' + name)
            for name in ('daily', 'sync', 'inspection', 'reminders', 'bot', 'notifications'))
    assert events[-1] == 'topics-close'


def test_routes_observe_background_state_and_conflicting_work(runtime, monkeypatch):
    background = load_background(monkeypatch)
    import httpx
    from qq_digest.candidates import CandidateService
    from qq_digest.knowledge import KnowledgeWriter
    from qq_digest.web.app import create_app

    started, release = threading.Event(), threading.Event()
    original_sleep = asyncio.sleep
    result = {'success': True, 'status': 'success', 'created_reports': 1}
    ticks = []

    async def tick(delay):
        ticks.append(delay)
        await original_sleep(0 if len(ticks) == 1 else 3600)

    async def idle(*args, **kwargs):
        await asyncio.Event().wait()

    def worker(config, **kwargs):
        started.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(background.asyncio, 'sleep', tick)
    monkeypatch.setattr(background, 'message_sync_loop', idle)
    monkeypatch.setattr(background, 'history_inspection_loop', idle)
    monkeypatch.setattr(background, 'reminder_loop', idle)
    monkeypatch.setattr('qq_digest.application_runner.run_daily_task', worker)
    app = create_app(archive=runtime.archive, candidates=CandidateService(runtime.archive),
        knowledge=KnowledgeWriter(runtime.config.knowledge_dir), config=runtime.config,
        password_hash='unused-synthetic-hash', session_secret='synthetic-session')

    async def scenario():
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://synthetic.local',
                    cookies={'qq_digest_session': app.state.cookie.issue()}) as client:
                    for _ in range(1000):
                        if started.is_set():
                            break
                        await original_sleep(.001)
                    assert started.is_set()
                    status = (await client.get('/api/scheduler')).json()
                    assert status['running'] is True
                    assert (await client.post('/api/run-daily')).status_code == 409
                    release.set()
                    for _ in range(1000):
                        status = (await client.get('/api/scheduler')).json()
                        if not status['running']:
                            break
                        await original_sleep(.001)
                    assert status['running'] is False and status['last_result'] == result
                    assert status['last_run_date'] == NOW.date().isoformat()
        finally:
            release.set()

    asyncio.run(scenario())
