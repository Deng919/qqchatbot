"""Synthetic storage/desktop regressions from the 2026-10-14 audit."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile

import pytest
import yaml

from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
from qq_digest.config import ConfigError, load_config
from qq_digest.desktop import make_server
from qq_digest.desktop_settings import DesktopBridge, migrate_storage
from qq_digest.desktop_updates import atomic_json, read_transaction, run_transaction
from tests.test_backup_restore import _data
from tests.test_desktop_updates import Runtime, env, prepare


@pytest.fixture(autouse=True)
def isolated_session_environment(monkeypatch):
    # Tests never read a real deployment's session environment value.
    monkeypatch.delenv('QQ_DIGEST_SESSION_SECRET', raising=False)


def test_desktop_rejects_empty_session_secret_before_opening_archive(tmp_path, monkeypatch):
    import qq_digest.desktop as module
    _, config = _data(tmp_path, 'source')
    raw = yaml.safe_load(config.read_text('utf-8'))
    raw['security'].pop('session_secret_file')
    raw['security']['session_secret_env'] = 'QQDIGEST_SYNTHETIC_ABSENT_SECRET'
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    monkeypatch.delenv('QQDIGEST_SYNTHETIC_ABSENT_SECRET', raising=False)
    opened = []
    real_open = module.Archive.open
    def track_open(path):
        archive = real_open(path)
        opened.append(archive)
        return archive
    monkeypatch.setattr(module.Archive, 'open', track_open)
    server = None
    try:
        with pytest.raises(ConfigError, match='Session secret'):
            server = make_server(config)
        assert opened == []
    finally:
        if server is not None:
            server.config.app.state.close_topic_tracking()
        for archive in opened:
            archive.close()


def test_migration_preserves_custom_relative_knowledge_and_session_files(tmp_path):
    source, config = _data(tmp_path, 'source')
    raw = yaml.safe_load(config.read_text('utf-8'))
    raw['knowledge']['resource_path'] = 'notes/resources.md'
    raw['security']['session_secret_file'] = 'private/session.txt'
    for relative, body in [('notes/resources.md', '# custom knowledge'),
                           ('private/session.txt', 'synthetic-local-session-secret-0123456789')]:
        path = source / relative
        path.parent.mkdir()
        path.write_text(body, encoding='utf-8')
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'launcher.json').write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'destination'
    migrate_storage(config, install, destination)
    for relative in ['notes/resources.md', 'private/session.txt']:
        assert (destination / relative).read_bytes() == (source / relative).read_bytes()
    assert load_config(destination / 'config/config.yaml').resolve_session_secret() == (source / 'private/session.txt').read_text()


@pytest.mark.parametrize('relative', [False, True])
def test_migration_keeps_external_custom_path_without_copying_it(tmp_path, relative):
    source, config = _data(tmp_path, 'source')
    external = tmp_path / 'external.md'
    external.write_text('synthetic external knowledge', encoding='utf-8')
    raw = yaml.safe_load(config.read_text('utf-8'))
    raw['knowledge']['resource_path'] = '../external.md' if relative else str(external)
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'launcher.json').write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'relocated' / 'destination'
    migrate_storage(config, install, destination)
    assert load_config(destination / 'config/config.yaml').resolve_knowledge_paths()['resource'] == external
    assert not (destination / external.name).exists()
    assert external.read_text() == 'synthetic external knowledge' and source.is_dir()


@pytest.mark.parametrize('interrupted_status', ['starting', 'committing', 'recovering'])
def test_recovery_intent_survives_a_second_worker_interruption(tmp_path, monkeypatch, interrupted_status):
    import qq_digest.desktop_updates as module
    registry, records, _, service, _ = env(tmp_path)
    prepare(service, records[1])
    transaction = read_transaction(registry)
    transaction.update(status=interrupted_status, child_pid=222, child_identity={'created': 1})
    atomic_json(registry.state_dir / 'transaction.json', transaction)
    real_atomic = module.atomic_json
    def terminate_after_checkpoint(path, payload):
        real_atomic(path, payload)
        raise KeyboardInterrupt('synthetic worker interruption')
    monkeypatch.setattr(module, 'atomic_json', terminate_after_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        run_transaction(registry, Runtime())
    assert read_transaction(registry)['status'] in {'starting', 'committing', 'recovering'}
    monkeypatch.setattr(module, 'atomic_json', real_atomic)
    runtime = Runtime()
    runtime.stop_existing = lambda pid, identity, target: runtime.stopped.append(pid)
    assert run_transaction(registry, runtime) == 1
    assert runtime.stopped == [222]
    assert runtime.launched == [('old', True)]


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction regression')
@pytest.mark.parametrize('operation', ['backup', 'migration'])
@pytest.mark.parametrize('placement', ['nested', 'source_ancestor'])
def test_storage_copy_rejects_source_junctions(tmp_path, operation, placement):
    source, config = _data(tmp_path, 'source')
    external = tmp_path / 'external'
    external.mkdir()
    sentinel = external / 'unrelated.txt'
    sentinel.write_text('synthetic unrelated content', encoding='utf-8')
    link = source / 'reports' / 'junction' if placement == 'nested' else tmp_path / 'source-alias'
    target = external if placement == 'nested' else source
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    install = tmp_path / 'install'
    install.mkdir()
    launcher = install / 'launcher.json'
    launcher.write_text(json.dumps({'config_path': str(config)}))
    configured = config if placement == 'nested' else link / 'config/config.yaml'
    try:
        with pytest.raises(ValueError, match='链接|重解析点'):
            if operation == 'backup':
                backup_data(configured, tmp_path / 'backups', temp_root=tmp_path / 'cache')
            else:
                migrate_storage(configured, install, tmp_path / 'destination')
        assert not (tmp_path / 'destination').exists()
        assert json.loads(launcher.read_text())['config_path'] == str(config)
        assert sentinel.read_text() == 'synthetic unrelated content'
    finally:
        assert link.absolute().is_relative_to(tmp_path.absolute())
        link.rmdir()


@pytest.mark.parametrize('invalid_secret', ['short', 'contains spaces ' + 'x' * 32, ''])
def test_restore_rejects_invalid_session_before_publishing_target(tmp_path, invalid_secret):
    source, config = _data(tmp_path, 'source')
    (source / 'config/secret.txt').write_text(invalid_secret, encoding='utf-8')
    backup = Path(backup_data(config, tmp_path / 'backups', temp_root=tmp_path / 'cache')['path'])
    preview = inspect_backup(backup, temp_root=tmp_path / 'cache')
    install = tmp_path / 'install'
    install.mkdir()
    launcher = install / 'launcher.json'
    launcher.write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'destination'
    with pytest.raises(ValueError, match='Session secret|配置'):
        restore_backup(backup, destination, config, install, backup_root=tmp_path / 'backups',
                       temp_root=tmp_path / 'cache', expected_sha256=preview['sha256'])
    assert not destination.exists()
    assert json.loads(launcher.read_text())['config_path'] == str(config)
    assert source.is_dir()


def test_update_bridge_omits_empty_qq_source_when_cwd_is_program_directory(tmp_path, monkeypatch):
    import qq_digest.desktop_settings as module
    import qq_digest.desktop_updates as updates
    registry, records, _, _, _ = env(tmp_path)
    _, config = _data(tmp_path, 'source')
    install = Path(records[0]['path'])
    (install / 'launcher.json').write_text(json.dumps({'config_path': str(config)}))
    monkeypatch.setattr(module, 'release_root', lambda: registry.root)
    monkeypatch.setattr(updates, 'STATE_DIR', registry.state_dir)
    monkeypatch.chdir(install)
    bridge = DesktopBridge(install, config, install / 'QQDigestDesktop.exe', gui=None,
                           backup_root=tmp_path / 'backups', temp_root=tmp_path / 'cache')
    service = bridge._updates()
    assert '' not in service.data_paths
    service.backup = lambda: {'path': 'synthetic-verified-backup.zip'}
    assert prepare(service, records[1])['status'] == 'prepared'


@pytest.mark.parametrize('env_secret', [None, 'short', 'synthetic-environment-session-secret-0123456789'])
def test_restore_validates_environment_session_before_publish(tmp_path, monkeypatch, env_secret):
    _, config = _data(tmp_path, 'source')
    raw = yaml.safe_load(config.read_text('utf-8'))
    raw['security'].pop('session_secret_file')
    raw['security']['session_secret_env'] = 'QQDIGEST_SYNTHETIC_RESTORE_SECRET'
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    if env_secret is None:
        monkeypatch.delenv('QQDIGEST_SYNTHETIC_RESTORE_SECRET', raising=False)
    else:
        monkeypatch.setenv('QQDIGEST_SYNTHETIC_RESTORE_SECRET', env_secret)
    backup = Path(backup_data(config, tmp_path / 'backups', temp_root=tmp_path / 'cache')['path'])
    preview = inspect_backup(backup, temp_root=tmp_path / 'cache')
    install = tmp_path / 'install'
    install.mkdir()
    launcher = install / 'launcher.json'
    launcher.write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'destination'
    def restore():
        return restore_backup(backup, destination, config, install, backup_root=tmp_path / 'backups',
                              temp_root=tmp_path / 'cache', expected_sha256=preview['sha256'])
    if env_secret is not None and len(env_secret) >= 32:
        assert restore()['restart_required'] is True
        assert load_config(destination / 'config/config.yaml', create_dirs=False).resolve_session_secret() == env_secret
    else:
        with pytest.raises(ValueError, match='Session secret'):
            restore()
        assert not destination.exists()
        assert json.loads(launcher.read_text())['config_path'] == str(config)


@pytest.mark.parametrize('absolute', [False, True])
def test_custom_managed_paths_survive_backup_and_restore(tmp_path, absolute):
    source, config = _data(tmp_path, 'source')
    raw = yaml.safe_load(config.read_text('utf-8'))
    for section, key, relative, body in [
        ('knowledge', 'resource_path', 'notes/resources.md', '# synthetic custom knowledge'),
        ('security', 'session_secret_file', 'private/session.txt', 'synthetic-local-secret-0123456789-abcdef'),
    ]:
        path = source / relative
        path.parent.mkdir()
        path.write_text(body, encoding='utf-8')
        raw[section][key] = str(path) if absolute else relative
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    backup = Path(backup_data(config, tmp_path / 'backups', temp_root=tmp_path / 'cache')['path'])
    preview = inspect_backup(backup, temp_root=tmp_path / 'cache')
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'launcher.json').write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'destination'
    restore_backup(backup, destination, config, install, backup_root=tmp_path / 'backups',
                   temp_root=tmp_path / 'cache', expected_sha256=preview['sha256'])
    restored = load_config(destination / 'config/config.yaml', create_dirs=False)
    assert restored.resolve_knowledge_paths()['resource'].read_text() == '# synthetic custom knowledge'
    assert restored.resolve_session_secret() == 'synthetic-local-secret-0123456789-abcdef'
    assert (destination / 'private/session.txt').read_bytes() == (source / 'private/session.txt').read_bytes()
    unexpected = tmp_path / 'unexpected.zip'
    with zipfile.ZipFile(backup) as original, zipfile.ZipFile(unexpected, 'w') as modified:
        for name in original.namelist():
            modified.writestr(name, original.read(name))
        modified.writestr('notes/undeclared.md', 'must not become a managed file')
    with pytest.raises(ValueError, match='路径'):
        inspect_backup(unexpected, temp_root=tmp_path / 'cache')


def test_restore_preserves_external_relative_reference_identity(tmp_path):
    source, config = _data(tmp_path, 'source')
    external = tmp_path / 'external.md'
    external.write_text('synthetic external knowledge', encoding='utf-8')
    raw = yaml.safe_load(config.read_text('utf-8'))
    raw['knowledge']['resource_path'] = '../external.md'
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    backup = Path(backup_data(config, tmp_path / 'backups', temp_root=tmp_path / 'cache')['path'])
    with zipfile.ZipFile(backup) as archive:
        assert 'external.md' not in archive.namelist()
    preview = inspect_backup(backup, temp_root=tmp_path / 'cache')
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'launcher.json').write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'elsewhere/destination'
    restore_backup(backup, destination, config, install, backup_root=tmp_path / 'backups',
                   temp_root=tmp_path / 'cache', expected_sha256=preview['sha256'])
    restored_path = load_config(destination / 'config/config.yaml', create_dirs=False).resolve_knowledge_paths()['resource']
    assert restored_path == external
    assert restored_path.read_text() == 'synthetic external knowledge'
    assert not (destination.parent / 'external.md').exists()
    assert source.is_dir()


def test_normalized_manifest_root_cannot_bypass_staged_session_validation(tmp_path):
    source, config = _data(tmp_path, 'source')
    private = source / 'private/session.txt'
    private.parent.mkdir()
    private.write_text('synthetic-original-live-secret-0123456789', encoding='utf-8')
    raw = yaml.safe_load(config.read_text('utf-8'))
    raw['security']['session_secret_file'] = str(private)
    config.write_text(yaml.safe_dump(raw), encoding='utf-8')
    backup = Path(backup_data(config, tmp_path / 'backups', temp_root=tmp_path / 'cache')['path'])
    changed = tmp_path / 'altered.zip'
    archived_secret = b'short'
    with zipfile.ZipFile(backup) as original, zipfile.ZipFile(changed, 'w') as target:
        manifest = json.loads(original.read('manifest.json'))
        manifest['source_data_dir'] = str(source / 'nonexistent') + '/..'
        manifest['files']['private/session.txt'] = {'sha256': hashlib.sha256(archived_secret).hexdigest(), 'size': len(archived_secret)}
        for name in original.namelist():
            body = archived_secret if name == 'private/session.txt' else json.dumps(manifest).encode() if name == 'manifest.json' else original.read(name)
            target.writestr(name, body)
    preview = inspect_backup(changed, temp_root=tmp_path / 'cache')
    install = tmp_path / 'install'
    install.mkdir()
    launcher = install / 'launcher.json'
    launcher.write_text(json.dumps({'config_path': str(config)}))
    destination = tmp_path / 'elsewhere/destination'
    with pytest.raises(ValueError, match='Session secret'):
        restore_backup(changed, destination, config, install, backup_root=tmp_path / 'backups',
                       temp_root=tmp_path / 'cache', expected_sha256=preview['sha256'])
    assert not destination.exists()
    assert json.loads(launcher.read_text())['config_path'] == str(config)
    assert private.read_text() == 'synthetic-original-live-secret-0123456789'


@pytest.mark.parametrize('invalid_root', ['relative/source', '', None, 123])
def test_manifest_source_root_must_be_absolute(tmp_path, invalid_root):
    _, config = _data(tmp_path, 'source')
    backup = Path(backup_data(config, tmp_path / 'backups', temp_root=tmp_path / 'cache')['path'])
    changed = tmp_path / 'invalid-root.zip'
    with zipfile.ZipFile(backup) as original, zipfile.ZipFile(changed, 'w') as target:
        manifest = json.loads(original.read('manifest.json'))
        manifest['source_data_dir'] = invalid_root
        for name in original.namelist():
            target.writestr(name, json.dumps(manifest).encode() if name == 'manifest.json' else original.read(name))
    with pytest.raises(ValueError, match='清单|源目录'):
        inspect_backup(changed, temp_root=tmp_path / 'cache')
