import importlib
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qq_digest.backup_restore import backup_data
from qq_digest.config import load_config
from tests.test_backup_restore import _data

NOW = datetime(2026, 10, 8, 4, tzinfo=timezone.utc)


def manager(tmp_path):
    module = importlib.import_module('qq_digest.storage_management')
    root, config = _data(tmp_path, 'source')
    backups = tmp_path / 'backups'
    cache = tmp_path / 'cache'
    backups.mkdir(); cache.mkdir()
    return module.StorageManager(load_config(config), backups, cache, now=lambda: NOW), config


def saved_backup(config, service, days, kind='automatic', owner=None):
    path = Path(backup_data(config, service.backup_root, temp_root=service.temp_root, kind=kind)['path'])
    with zipfile.ZipFile(path) as archive:
        contents = {item.filename: archive.read(item.filename) for item in archive.infolist()}
    metadata = json.loads(contents['manifest.json'])
    metadata['created_at'] = (NOW - timedelta(days=days)).isoformat()
    if owner: metadata['source_data_dir'] = str(owner)
    contents['manifest.json'] = json.dumps(metadata).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in contents.items(): archive.writestr(name, data)
    return path


def abandoned_cache(service, days, *, marked=True):
    root = service.temp_root / ('qqdigest-backup-' + str(days))
    root.mkdir()
    (root / 'data.txt').write_text('cache only')
    if marked:
        (root / '.qqdigest-cache.json').write_text(json.dumps({
            'version': 1, 'owner': str(service.config.data_dir.resolve()),
            'prefix': 'qqdigest-backup-', 'created_at': (NOW-timedelta(days=days)).isoformat(),
        }), encoding='utf-8')
    return root


def test_retention_preserves_three_verified_and_manual_backups(tmp_path):
    service, config = manager(tmp_path)
    saved = [saved_backup(config, service, days) for days in (50, 45, 40, 35, 31)]
    manual = saved_backup(config, service, 100, 'manual')
    safety = saved_backup(config, service, 100, 'before-restore')
    other = saved_backup(config, service, 100, owner=tmp_path / 'other')
    preview = service.preview()
    assert {Path(item['path']) for item in preview['candidates']} == set(saved[:2])
    result = service.clean(preview['token'])
    assert result['deleted_count'] == 2
    assert result['status'] == 'success'
    assert all(path.exists() for path in saved[2:] + [manual, safety, other])
    assert not any(path.exists() for path in saved[:2])


def test_insufficient_valid_backups_and_recent_age_protected(tmp_path):
    service, config = manager(tmp_path)
    old = saved_backup(config, service, 40)
    saved_backup(config, service, 2)
    broken = service.backup_root / 'QQDigest-automatic-broken.zip'
    broken.write_bytes(b'invalid')
    assert not service.preview()['candidates']
    assert old.exists() and broken.exists()


def test_cache_requires_ownership_age_and_no_active_lease(tmp_path):
    service, _ = manager(tmp_path)
    files = importlib.import_module('qq_digest.storage_files')
    old = abandoned_cache(service, 8)
    recent = abandoned_cache(service, 1)
    unknown = abandoned_cache(service, 9, marked=False)
    with files.cache_lock(old):
        assert not service.preview()['candidates']
    preview = service.preview()
    assert [Path(item['path']) for item in preview['candidates']] == [old]
    result = service.clean(preview['token'])
    assert result['deleted_count'] == 1
    assert not old.exists() and recent.exists() and unknown.exists()


def test_preview_change_policy_expiry_and_file_change_never_delete(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    preview = service.preview()
    (old / 'data.txt').write_text('changed after preview')
    result = service.clean(preview['token'])
    assert result['deleted_count'] == 0 and result['skipped']
    preview = service.preview()
    values = {k: v for k,v in service.policy.settings().items() if k in ('enabled','backup_days','backup_keep','cache_days')}
    values['cache_days'] = 14
    service.policy.update(values, 0)
    with pytest.raises(ValueError, match='策略|预览'): service.clean(preview['token'])
    preview = service.preview()
    service.now = lambda: NOW + timedelta(minutes=11)
    with pytest.raises(ValueError, match='过期'): service.clean(preview['token'])
    assert old.exists()


def test_keep_backup_removed_after_preview_blocks_backup_cleanup(tmp_path):
    service, config = manager(tmp_path)
    saved = [saved_backup(config, service, days) for days in (50, 45, 40, 35)]
    preview = service.preview()
    saved[-1].unlink()
    result = service.clean(preview['token'])
    assert result['deleted_count'] == 0 and result['skipped']
    assert saved[0].exists()


def test_cache_with_hardlink_or_link_is_preserved(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    os.link(old / 'data.txt', tmp_path / 'outside.txt')
    assert not service.preview()['candidates']
    assert (tmp_path / 'outside.txt').read_text() == 'cache only'


def test_delete_failure_restores_original_and_records_partial(tmp_path, monkeypatch):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    preview = service.preview()
    def fail(*args, **kwargs): raise OSError('blocked')
    monkeypatch.setattr(shutil, 'rmtree', fail)
    result = service.clean(preview['token'])
    assert old.exists() and result['status'] == 'partial'
    assert result['errors'] and result['deleted_count'] == 0
    assert service.policy.last_result()['status'] == 'partial'


def test_snapshot_does_not_create_missing_cache_or_backup_root(tmp_path):
    service, _ = manager(tmp_path)
    service.backup_root.rmdir(); service.temp_root.rmdir()
    service.snapshot()
    assert not service.backup_root.exists() and not service.temp_root.exists()


def test_partial_cache_delete_counts_actual_removed_bytes(tmp_path, monkeypatch):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    preview = service.preview()
    def fail(path, **kwargs):
        (Path(path) / 'data.txt').unlink()
        raise OSError('partly blocked')
    monkeypatch.setattr(shutil, 'rmtree', fail)
    result = service.clean(preview['token'])
    assert result['status'] == 'partial'
    assert result['deleted_bytes'] == len('cache only')
    assert old.exists()


def test_busy_root_is_not_recorded_as_daily_success(tmp_path):
    service, _ = manager(tmp_path)
    files = importlib.import_module('qq_digest.storage_files')
    old = abandoned_cache(service, 8)
    with files.cache_lock(old):
        with pytest.raises(files.StorageBusy): service.maybe_automatic()
    assert service.policy.automatic_due(NOW)


def test_recovery_restores_interrupted_cache_isolation_without_deletion(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    preview = service.preview()
    item = preview['candidates'][0]
    identity = 'a' * 32
    quarantine = old.parent / ('.qqdigest-trash-' + identity)
    journal = old.parent / ('.qqdigest-cleanup-' + identity + '.json')
    journal.write_text(json.dumps({'version':1,'owner':str(service.owner),'original':old.name,
        'quarantine':quarantine.name,'kind':'cache','fingerprint':item['fingerprint']}))
    old.rename(quarantine)
    service.preview()
    assert old.exists() and not quarantine.exists() and not journal.exists()


def test_user_report_inside_abandoned_cache_is_protected(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    with sqlite3.connect(service.config.archive_path) as db:
        db.execute('UPDATE reports SET markdown_path=?', (str(old / 'data.txt'),))
    assert not service.preview()['candidates']
    assert old.exists()


def test_relative_report_reference_inside_cache_is_protected(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    # Store a valid relative reference using the same base as inventory.
    service.config.work_dir.mkdir(exist_ok=True)
    local = service.config.work_dir / 'snapshots'
    local.mkdir()
    moved = local / old.name
    old.rename(moved)
    with sqlite3.connect(service.config.archive_path) as db:
        db.execute('UPDATE reports SET markdown_path=?', (str((moved / 'data.txt').relative_to(service.owner)),))
    assert not service.preview()['candidates']


def test_relative_backup_owner_is_not_adopted(tmp_path, monkeypatch):
    service, config = manager(tmp_path)
    monkeypatch.chdir(tmp_path)
    saved = [saved_backup(config, service, days, owner=Path('source')) for days in (50,45,40,35)]
    assert not service.preview()['candidates']
    assert all(path.exists() for path in saved)


def test_malformed_manifest_and_recovery_record_do_not_abort_cache_preview(tmp_path):
    service, _ = manager(tmp_path)
    malformed = service.backup_root / 'QQDigest-automatic-malformed.zip'
    with zipfile.ZipFile(malformed, 'w') as archive: archive.writestr('manifest.json', '[]')
    journal = service.temp_root / ('.qqdigest-cleanup-' + 'a'*32 + '.json')
    journal.write_text('[]')
    old = abandoned_cache(service, 8)
    preview = service.preview()
    assert [Path(item['path']) for item in preview['candidates']] == [old]
    assert len(preview['protected']) >= 2


def test_archive_lock_precedes_preview_mutex_to_avoid_scheduler_deadlock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    service, _ = manager(tmp_path)
    module = importlib.import_module('qq_digest.storage_management')
    held = []
    original_root = module.root_lock
    @contextmanager
    def guarded_root(path):
        if Path(path) == service.config.archive_path.parent:
            assert not held, 'preview holds mutex while waiting for automatic archive lock'
        with original_root(path): yield
    @contextmanager
    def mutex():
        held.append(True)
        try: yield
        finally: held.pop()
    monkeypatch.setattr(module, 'root_lock', guarded_root)
    service._guard = mutex()
    service.preview()


def test_replacing_archive_invalidates_old_preview(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    preview = service.preview()
    replacement = service.config.archive_path.with_suffix('.replacement')
    shutil.copyfile(service.config.archive_path, replacement)
    os.replace(replacement, service.config.archive_path)
    with pytest.raises(ValueError, match='存储|预览'): service.clean(preview['token'])
    assert old.exists()


def test_clock_rollback_invalidates_cleanup_age_decision(tmp_path):
    service, _ = manager(tmp_path)
    old = abandoned_cache(service, 8)
    preview = service.preview()
    service.now = lambda: NOW - timedelta(days=2)
    with pytest.raises(ValueError, match='时间|预览'): service.clean(preview['token'])
    assert old.exists()
