import json
from datetime import datetime, timezone

import pytest

from qq_digest.desktop_settings import DesktopBridge
from qq_digest.operations import OperationCoordinator
from tests.test_desktop_settings import _source
from tests.test_web import web_client


def bridge_for(tmp_path):
    _, config, install = _source(tmp_path)
    bridge = DesktopBridge(install, config, install / 'program.exe', gui=None,
                           backup_root=tmp_path / 'backups', temp_root=tmp_path / 'cache')
    bridge._operations = OperationCoordinator()
    return bridge


def test_bridge_reuses_manager_and_invalidates_after_storage_switch(tmp_path):
    bridge = bridge_for(tmp_path)
    assert hasattr(bridge, 'get_storage'), 'storage bridge is missing'
    assert bridge.get_storage()['policy']['backup_days'] == 30
    assert bridge._storage() is bridge._storage()
    (bridge._install_dir / 'launcher.json').write_text(json.dumps({'config_path': str(tmp_path / 'other.yaml')}))
    # The old runtime remains readable after a handoff; cleanup is paused.
    assert bridge.get_storage()['policy']['backup_days'] == 30
    from qq_digest.operations import OperationBusy
    with pytest.raises(OperationBusy, match='重启'):
        bridge.preview_storage_cleanup()
    assert bridge.maybe_scheduled_cleanup() is None


def test_bridge_mutations_obey_operation_and_backup_disabled_cleanup_independent(tmp_path):
    bridge = bridge_for(tmp_path)
    assert hasattr(bridge, 'maybe_scheduled_cleanup'), 'cleanup scheduling is missing'
    bridge.set_backup_schedule('off')
    assert bridge.maybe_scheduled_backup() is None
    with bridge._operations.claim('collect'):
        assert bridge.maybe_scheduled_cleanup() is None
        with pytest.raises(Exception, match='运行|进行'):
            bridge.preview_storage_cleanup()
    assert bridge.maybe_scheduled_cleanup()['status'] == 'success'
    assert bridge.maybe_scheduled_cleanup() is None
    settings = bridge.get_storage()['policy']
    values = {k:v for k,v in settings.items() if k != 'revision'}
    values['enabled'] = False
    bridge.update_storage_policy(values, settings['revision'])
    assert bridge.maybe_scheduled_cleanup() is None


def test_storage_routes_auth_no_bridge_strict_input_and_cleanup(tmp_path):
    client, _, _ = web_client.__wrapped__(tmp_path)
    assert client.get('/api/storage').status_code == 401
    assert client.put('/api/storage/policy', json={}).status_code == 401
    assert client.post('/api/storage/preview').status_code == 401
    client.post('/login', data={'password':'password123'})
    assert client.get('/api/storage').status_code == 503
    client.app.state.desktop_bridge = bridge_for(tmp_path / 'desktop')
    response = client.get('/api/storage')
    assert response.status_code == 200
    payload = dict(enabled=True, backup_days=30, backup_keep=3, cache_days=7, expected_revision=0)
    assert client.put('/api/storage/policy', json=dict(payload, backup_days=True)).status_code == 422
    assert client.put('/api/storage/policy', json=dict(payload, path='D:/')).status_code == 422
    assert client.put('/api/storage/policy', json=payload).status_code == 200
    assert client.put('/api/storage/policy', json=payload).status_code == 409
    with client.app.state.operations.claim('backup'):
        assert client.post('/api/storage/preview').status_code == 409
    preview = client.post('/api/storage/preview').json()
    assert client.post('/api/storage/clean', json={'token': preview['token'], 'path': 'D:/'}).status_code == 422
    assert client.post('/api/storage/clean', json={'token': preview['token']}).status_code == 200
    assert client.post('/api/storage/clean', json={'token': preview['token']}).status_code == 422


def test_message_sync_registers_refresh_cache_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from qq_digest.config import load_config
    from qq_digest.sync import run_message_sync
    from tests.test_backup_restore import _data
    root, config = _data(tmp_path, 'sync-owner')
    cfg = load_config(config)
    cfg.ntqq.enabled = True; cfg.ntqq.db_dir = str(tmp_path / 'qq-source')
    calls = []
    def refresh(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=False, message='synthetic stop')
    monkeypatch.setattr('qq_digest.refresh.refresh_database', refresh)
    run_message_sync(config=cfg)
    assert calls[0].get('cache_owner') == cfg.data_dir


def test_other_process_collect_claim_blocks_automatic_cleanup(tmp_path):
    import subprocess
    import sys
    service, _ = __import__('tests.test_storage_management', fromlist=['manager']).manager(tmp_path)
    code = '''from pathlib import Path
import sys
from qq_digest.operations import OperationCoordinator
with OperationCoordinator(activity_root=lambda:Path(sys.argv[1])).claim('collect'):
 print('ready', flush=True)
 sys.stdin.readline()
'''
    process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code, str(service.owner)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        from qq_digest.storage_files import StorageBusy
        with pytest.raises(StorageBusy): service.maybe_automatic()
    finally:
        process.communicate('\n', timeout=10)
    assert service.policy.automatic_due()


def test_operation_lease_can_be_released_by_version_watcher_thread(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    operations = OperationCoordinator(activity_root=lambda:tmp_path)
    claim = operations.claim('version_mutation')
    claim.__enter__()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(claim.__exit__, None, None, None).result(timeout=3)
    with operations.claim('storage_mutation'): pass


def test_cli_operation_refuses_another_thread_activity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from qq_digest import cli
    from qq_digest.storage_files import activity_lock
    from tests.test_backup_restore import _data
    import typer
    root, config = _data(tmp_path, 'cli-activity')
    assert hasattr(cli, '_storage_command'), 'CLI activity guard is missing'
    calls = []
    @cli._storage_command
    def command(config_path): calls.append(config_path)
    with activity_lock(root), ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(typer.Exit): pool.submit(command, config_path=config).result(timeout=3)
    assert calls == []
    command(config_path=config)
    assert calls == [config]
