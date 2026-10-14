from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.test_web_background_tasks import (
    NOW, daily_loop, load_background, run_cycles, runtime, save_job,
)


def recovery_loop(monkeypatch, background, runtime, kind, worker):
    if kind == 'sync':
        monkeypatch.setattr('qq_digest.sync.run_message_sync', worker)
        return lambda: background.message_sync_loop(
            runtime.config, runtime.operations, runtime.features, runtime.sync)
    if kind == 'catchup':
        monkeypatch.setattr(background, 'pending_catchup_date',
                            lambda *args, **kwargs: NOW.date() - timedelta(days=1))
    return lambda: daily_loop(background, runtime, worker)


@pytest.mark.parametrize('kind', ['daily', 'catchup', 'sync'])
def test_background_worker_exception_does_not_permanently_stop_future_cycles(
    runtime, monkeypatch, caplog, kind,
):
    background = load_background(monkeypatch)
    calls = []
    state = runtime.sync if kind == 'sync' else runtime.daily
    state['last_result'] = {'success': True, 'status': 'success'}
    result = {'success': True, 'status': 'success'}

    def worker(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('synthetic transient storage failure')
        if kind == 'catchup':
            save_job(runtime.archive, (NOW.date() - timedelta(days=1)).isoformat())
        return SimpleNamespace(to_dict=lambda: result) if kind == 'sync' else result

    def before_tick(index):
        if index == 2:
            assert state['last_result']['success'] is False
            assert 'synthetic transient storage failure' in str(state['last_result'])
            assert state['running'] is False
            assert not runtime.operations.is_active('sync' if kind == 'sync' else 'daily')

    loop = recovery_loop(monkeypatch, background, runtime, kind, worker)
    ticks = run_cycles(monkeypatch, background, loop, cycles=2, before_tick=before_tick)
    assert len(calls) == 2
    assert state['last_result'] == result
    assert state['running'] is False
    assert not runtime.operations.is_active('sync' if kind == 'sync' else 'daily')
    assert ticks == ([runtime.config.collection.startup_delay_seconds, 600, 600]
                     if kind == 'sync' else [60, 60, 60])
    assert any(record.exc_info for record in caplog.records)
    assert 'synthetic transient storage failure' in caplog.text


@pytest.mark.parametrize('phase', ['pending_catchup_date', 'daily_retry_state', 'daily_feature', 'sync_feature'])
def test_decision_read_failure_is_visible_and_next_cycle_recovers(runtime, monkeypatch, caplog, phase):
    background = load_background(monkeypatch)
    kind = 'sync' if phase == 'sync_feature' else 'daily'
    state = runtime.sync if kind == 'sync' else runtime.daily
    state['last_result'] = {'success': True}
    reads, calls = [], []
    if phase.endswith('_feature'):
        original = runtime.features.enabled
    else:
        original = getattr(background, phase)

    def read(*args, **kwargs):
        reads.append(1)
        if len(reads) == 1:
            raise OSError('synthetic scheduling read failure')
        return original(*args, **kwargs)

    if phase.endswith('_feature'):
        monkeypatch.setattr(runtime.features, 'enabled', read)
    else:
        monkeypatch.setattr(background, phase, read)

    result = {'success': True}

    def worker(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(to_dict=lambda: result) if kind == 'sync' else result

    def before_tick(index):
        if index == 2:
            assert state['last_result']['success'] is False
            assert 'synthetic scheduling read failure' in str(state['last_result'])
            assert not state['running']
            assert not runtime.operations.is_active(kind)
            assert state.get('last_attempt_date', '') == ''

    loop = recovery_loop(monkeypatch, background, runtime, kind, worker)
    run_cycles(monkeypatch, background, loop, cycles=2, before_tick=before_tick)
    assert len(reads) == 2 and calls == [1]
    assert state['last_result'] == result
    assert 'synthetic scheduling read failure' in caplog.text


@pytest.mark.parametrize('kind', ['daily', 'catchup', 'sync'])
def test_continuous_failures_keep_existing_cycle_interval(runtime, monkeypatch, kind):
    background = load_background(monkeypatch)
    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise OSError('synthetic persistent storage failure')

    loop = recovery_loop(monkeypatch, background, runtime, kind, broken)
    ticks = run_cycles(monkeypatch, background, loop, cycles=3)
    assert len(calls) == 3
    assert ticks == ([runtime.config.collection.startup_delay_seconds, 600, 600, 600]
                     if kind == 'sync' else [60, 60, 60, 60])


@pytest.mark.parametrize('attempts', [1, 3])
def test_unexpected_failure_keeps_persisted_daily_retry_limits(runtime, monkeypatch, attempts):
    background = load_background(monkeypatch)
    calls = []

    def broken(config):
        calls.append(1)
        for _ in range(attempts):
            save_job(runtime.archive, NOW.date().isoformat(), 'failed', NOW)
        raise OSError('synthetic failure after recording attempt')

    run_cycles(monkeypatch, background, lambda: daily_loop(background, runtime, broken), cycles=3)
    assert calls == [1]
    assert runtime.daily['last_result']['success'] is False
    assert runtime.daily['last_run_date'] == ''
