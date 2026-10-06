import json
from pathlib import Path

import pytest

from qq_digest.desktop_updates import UpdateService, run_transaction, atomic_json
from qq_digest.desktop_releases import ReleaseRegistry, write_release


def env(tmp_path):
    root = tmp_path / 'apps'
    state = tmp_path / 'state'
    root.mkdir()
    config = tmp_path / 'data' / 'config' / 'config.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('security:\n  web_password_hash: test\n', encoding='utf-8')
    registry = ReleaseRegistry(root, state)
    records = []
    for version in ('old', 'new'):
        directory = root / version
        directory.mkdir()
        (directory / 'QQDigestDesktop.exe').write_bytes(version.encode())
        (directory / 'launcher.json').write_text(json.dumps({'config_path': str(config)}))
        write_release(directory, version, version)
        records.append(registry.register(directory))
    backups = []
    def backup():
        backups.append(True)
        return {'path': 'verified-backup.zip'}
    service = UpdateService(registry, records[0]['path'], config, backup,
                            data_paths=[config.parent.parent])
    return registry, records, config, service, backups


class Runtime:
    def __init__(self, result=True, parent=True):
        self.result, self.parent = result, parent
        self.launched, self.stopped, self.entries = [], [], []
    def wait_parent(self, pid):
        return self.parent
    def launch(self, record, transaction, *, recovery=False):
        self.launched.append((record['version'], recovery))
        return len(self.launched) + 100
    def confirm(self, child, record, transaction, *, recovery=False):
        return True if recovery else self.result
    def stop(self, child):
        self.stopped.append(child)
    def switch_entries(self, transaction, target):
        self.entries.append(target['version'])
    def port_free(self, url):
        return True


def prepare(service, target):
    return service.prepare(target['version'], target['digest'], parent_pid=123,
                           url='http://127.0.0.1:7654/')


def test_success_keeps_data_and_commits_program_pointers(tmp_path):
    registry, records, config, service, backups = env(tmp_path)
    content = config.read_bytes()
    prepare(service, records[1])
    runtime = Runtime()
    assert run_transaction(registry, runtime) == 0
    state = registry.read_state()
    assert state['current'] == 'new' and state['rollback'] == 'old'
    assert config.read_bytes() == content and backups == [True]
    assert runtime.entries == ['new']
    assert json.loads((registry.state_dir / 'transaction.json').read_text())['status'] == 'success'


def test_failed_launch_stops_target_then_recovers_old_program(tmp_path):
    registry, records, config, service, _ = env(tmp_path)
    prepare(service, records[1])
    runtime = Runtime(result=False)
    assert run_transaction(registry, runtime) == 1
    assert runtime.stopped == [101]
    assert runtime.launched == [('new', False), ('old', True)]
    assert registry.read_state()['current'] == 'old'
    assert config.exists()
    assert json.loads((registry.state_dir / 'transaction.json').read_text())['status'] == 'rolled_back'


def test_parent_still_running_never_starts_another_writer(tmp_path):
    registry, records, _, service, _ = env(tmp_path)
    prepare(service, records[1])
    runtime = Runtime(parent=False)
    assert run_transaction(registry, runtime) == 1
    assert runtime.launched == []


def test_external_config_change_cancels_switch(tmp_path):
    registry, records, config, service, _ = env(tmp_path)
    prepare(service, records[1])
    config.write_text('changed')
    runtime = Runtime()
    assert run_transaction(registry, runtime) == 1
    assert runtime.launched == [('old', True)]
    assert config.read_text() == 'changed'


def test_backup_failure_does_not_create_pending_transaction(tmp_path):
    registry, records, _, service, _ = env(tmp_path)
    def fail():
        raise OSError('backup failed')
    service.backup = fail
    with pytest.raises(OSError):
        prepare(service, records[1])
    assert not (registry.state_dir / 'transaction.json').exists()


def test_modified_target_and_incompatible_package_rejected_before_backup(tmp_path):
    registry, records, _, service, backups = env(tmp_path)
    target = Path(records[1]['path'])
    (target / 'QQDigestDesktop.exe').write_bytes(b'bad')
    with pytest.raises(ValueError):
        prepare(service, records[1])
    assert backups == []
    write_release(target, 'future', 'future', compatibility='future')
    changed = registry.register(target)
    with pytest.raises(ValueError, match='兼容'):
        prepare(service, changed)
    assert backups == []


def test_pending_transaction_blocks_second_prepare(tmp_path):
    _, records, _, service, backups = env(tmp_path)
    prepare(service, records[1])
    with pytest.raises(ValueError):
        prepare(service, records[1])
    assert backups == [True]


def test_target_changed_after_backup_is_not_launched(tmp_path):
    registry, records, _, service, _ = env(tmp_path)
    prepare(service, records[1])
    (Path(records[1]['path']) / 'QQDigestDesktop.exe').write_bytes(b'changed')
    runtime = Runtime()
    assert run_transaction(registry, runtime) == 1
    assert runtime.launched == [('old', True)]


def test_interrupted_worker_recovers_without_retrying_target(tmp_path):
    registry, records, _, service, _ = env(tmp_path)
    prepare(service, records[1])
    transaction = json.loads((registry.state_dir / 'transaction.json').read_text())
    transaction.update(status='starting', child_pid=222, child_identity={'created': 1})
    atomic_json(registry.state_dir / 'transaction.json', transaction)
    runtime = Runtime()
    runtime.stop_existing = lambda pid, identity, target: runtime.stopped.append(pid)
    assert run_transaction(registry, runtime) == 1
    assert runtime.stopped == [222]
    assert runtime.launched == [('old', True)]


def test_bridge_keeps_operation_lock_until_shutdown(tmp_path, monkeypatch):
    from qq_digest.desktop_settings import DesktopBridge
    from qq_digest.operations import OperationCoordinator, OperationBusy
    registry, records, config, service, _ = env(tmp_path)
    bridge = DesktopBridge(Path(records[0]['path']), config, Path(records[0]['path'])/'QQDigestDesktop.exe', gui=None)
    bridge._operations = OperationCoordinator()
    bridge._owns_service = True
    bridge._window = object()
    bridge._runtime_url = 'http://127.0.0.1:7654/'
    monkeypatch.setattr(bridge, '_updates', lambda: service)
    monkeypatch.setattr(bridge, '_start_update_worker', lambda service: None)
    bridge.switch_version('new', records[1]['digest'])
    with pytest.raises(OperationBusy):
        with bridge._operations.claim('collect'):
            pass
    bridge.release_update_claim()
    assert bridge._operations.can_start('collect')


def test_bridge_failed_worker_launch_releases_lock(tmp_path, monkeypatch):
    from qq_digest.desktop_settings import DesktopBridge
    from qq_digest.operations import OperationCoordinator
    registry, records, config, service, _ = env(tmp_path)
    bridge = DesktopBridge(Path(records[0]['path']), config, Path(records[0]['path'])/'QQDigestDesktop.exe', gui=None)
    bridge._operations = OperationCoordinator()
    bridge._owns_service = True
    bridge._window = object()
    bridge._runtime_url = 'http://127.0.0.1:7654/'
    monkeypatch.setattr(bridge, '_updates', lambda: service)
    def fail(service):
        raise OSError('worker failed')
    monkeypatch.setattr(bridge, '_start_update_worker', fail)
    with pytest.raises(OSError):
        bridge.switch_version('new', records[1]['digest'])
    assert bridge._operations.can_start('collect')
    assert json.loads((registry.state_dir/'transaction.json').read_text())['status'] == 'canceled'


def test_recovery_launch_must_be_confirmed(tmp_path):
    registry, records, _, service, _ = env(tmp_path)
    prepare(service, records[1])
    runtime = Runtime(result=False)
    runtime.confirm = lambda *args, **kwargs: False
    assert run_transaction(registry, runtime) == 1
    assert json.loads((registry.state_dir/'transaction.json').read_text())['status'] == 'recovery_failed'


def test_unverified_live_target_never_starts_recovery_writer(tmp_path):
    registry, records, _, service, _ = env(tmp_path)
    prepare(service, records[1])
    transaction = json.loads((registry.state_dir/'transaction.json').read_text())
    transaction.update(status='starting', child_pid=222, child_identity=None)
    atomic_json(registry.state_dir/'transaction.json', transaction)
    runtime = Runtime()
    def fail(*args):
        raise RuntimeError('target remains alive')
    runtime.stop_existing = fail
    assert run_transaction(registry, runtime) == 1
    assert runtime.launched == []
    assert json.loads((registry.state_dir/'transaction.json').read_text())['status'] == 'recovery_failed'


def test_failed_second_prepare_keeps_existing_transaction(tmp_path, monkeypatch):
    from qq_digest.desktop_settings import DesktopBridge
    from qq_digest.operations import OperationCoordinator
    registry, records, config, service, _ = env(tmp_path)
    prepare(service, records[1])
    before = (registry.state_dir/'transaction.json').read_bytes()
    bridge = DesktopBridge(Path(records[0]['path']), config, Path(records[0]['path'])/'QQDigestDesktop.exe', gui=None)
    bridge._operations = OperationCoordinator()
    bridge._owns_service = True
    bridge._window = object()
    bridge._runtime_url = 'http://127.0.0.1:7654/'
    monkeypatch.setattr(bridge, '_updates', lambda: service)
    with pytest.raises(ValueError):
        bridge.switch_version('new', records[1]['digest'])
    assert (registry.state_dir/'transaction.json').read_bytes() == before


def test_old_worker_completion_cannot_release_new_operation(tmp_path):
    from qq_digest.desktop_settings import DesktopBridge
    from qq_digest.operations import OperationCoordinator
    bridge = DesktopBridge(tmp_path, tmp_path/'config.yaml', tmp_path/'app.exe', gui=None)
    bridge._operations = OperationCoordinator()
    old = bridge._operations.claim('version_mutation')
    old.__enter__();bridge._update_claim = old
    bridge.release_update_claim(expected=old)
    new = bridge._operations.claim('version_mutation')
    new.__enter__();bridge._update_claim = new
    bridge.release_update_claim(expected=old)
    assert not bridge._operations.can_start('collect')
    bridge.release_update_claim(expected=new)
    assert bridge._operations.can_start('collect')
