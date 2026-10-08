from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from qq_digest.storage_policy import StorageConflict, StoragePolicy


VALUES = {'enabled': True, 'backup_days': 30, 'backup_keep': 3, 'cache_days': 7}


def test_defaults_persist_and_updates_compare_revision(tmp_path):
    path = tmp_path / 'archive.sqlite'
    first, second = StoragePolicy(path), StoragePolicy(path)
    assert first.settings() == dict(VALUES, revision=0)
    assert second.update(dict(VALUES, cache_days=10), 0)['revision'] == 1
    with pytest.raises(StorageConflict):
        first.update(VALUES, 0)
    assert StoragePolicy(path).settings()['cache_days'] == 10


@pytest.mark.parametrize('field,value', [
    ('enabled', 1), ('enabled', 'true'), ('backup_days', True), ('backup_days', 0),
    ('backup_days', 3651), ('cache_days', 1.5), ('backup_keep', 0), ('backup_keep', 101),
])
def test_strict_policy_validation(tmp_path, field, value):
    policy = StoragePolicy(tmp_path / 'archive.sqlite')
    with pytest.raises(ValueError):
        policy.update(dict(VALUES, **{field: value}), 0)
    assert policy.settings()['revision'] == 0


@pytest.mark.parametrize('values,revision', [
    ({'enabled': True}, 0), (dict(VALUES, extra=1), 0), (VALUES, True), (VALUES, -1),
])
def test_exact_fields_and_revision(tmp_path, values, revision):
    with pytest.raises(ValueError):
        StoragePolicy(tmp_path / 'archive.sqlite').update(values, revision)


def test_successful_automatic_cadence_survives_restart_and_later_failures(tmp_path):
    path = tmp_path / 'archive.sqlite'
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    policy = StoragePolicy(path, now=lambda: now)
    assert policy.automatic_due()
    policy.record({'status': 'success', 'deleted_count': 2}, automatic=True)
    now += timedelta(hours=23)
    assert not StoragePolicy(path, now=lambda: now).automatic_due()
    policy.record({'status': 'failed'}, automatic=True)
    policy.record({'status': 'success'}, automatic=False)
    now += timedelta(hours=1)
    assert StoragePolicy(path, now=lambda: now).automatic_due()
    policy.update(dict(VALUES, enabled=False), 0)
    assert not policy.automatic_due(now + timedelta(days=10))


def test_result_is_bounded_safe_json_and_latest_result_is_saved(tmp_path):
    path = tmp_path / 'archive.sqlite'
    policy = StoragePolicy(path)
    assert policy.last_result() is None
    policy.record({'status': 'failed', 'errors': ['x' * 5000] * 1000, 'token': 'private'}, automatic=False)
    result = StoragePolicy(path).last_result()
    assert result['status'] == 'failed'
    assert 'token' not in result
    assert len(json.dumps(result)) < 300_000
    policy.record({'status': 'success', 'deleted_bytes': 123}, automatic=False)
    assert policy.last_result()['deleted_bytes'] == 123
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='storage_retention_settings'").fetchone()


def test_naive_clock_rejected(tmp_path):
    policy = StoragePolicy(tmp_path / 'archive.sqlite', now=lambda: datetime(2026, 10, 8))
    with pytest.raises(ValueError):
        policy.record({'status': 'success'}, automatic=True)


def test_concurrent_updates_have_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = tmp_path / 'archive.sqlite'
    StoragePolicy(path).settings()
    barrier = Barrier(2)
    def update(days):
        barrier.wait()
        try:
            return StoragePolicy(path).update(dict(VALUES, cache_days=days), 0)
        except StorageConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [10, 20]))
    assert sum(result is not None for result in results) == 1
    assert StoragePolicy(path).settings()['revision'] == 1


def test_failed_automatic_and_manual_success_do_not_delay_first_automatic(tmp_path):
    policy = StoragePolicy(tmp_path / 'archive.sqlite')
    policy.record({'status': 'failed'}, automatic=True)
    policy.record({'status': 'partial'}, automatic=True)
    policy.record({'status': 'success'}, automatic=False)
    assert policy.automatic_due()
