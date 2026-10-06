from pathlib import Path
import json
import pytest
from qq_digest.desktop_releases import ReleaseRegistry, write_release


def package(tmp_path, version='v1'):
    root = tmp_path / 'apps'
    directory = root / version
    directory.mkdir(parents=True)
    (directory / 'QQDigest.exe').write_bytes(b'program')
    write_release(directory, version, 'backend')
    registry = ReleaseRegistry(root, tmp_path / 'state')
    record = registry.register(directory)
    return registry, directory, record


def test_register_verify_snapshot(tmp_path):
    registry, directory, record = package(tmp_path)
    assert registry.verify('v1', record['digest']) == record
    assert registry.snapshot('v1', [])['current'] == 'v1'
    assert record['size_bytes'] == 7


@pytest.mark.parametrize('change', ['modified', 'extra', 'missing'])
def test_payload_changes_rejected(tmp_path, change):
    registry, directory, record = package(tmp_path)
    if change == 'modified':
        (directory / 'QQDigest.exe').write_bytes(b'changed')
    elif change == 'extra':
        (directory / 'extra.dll').write_bytes(b'extra')
    else:
        (directory / 'QQDigest.exe').unlink()
    with pytest.raises(ValueError):
        registry.verify('v1', record['digest'])


@pytest.mark.parametrize('pointer', ['current', 'rollback'])
def test_clean_protects_state_pointers(tmp_path, pointer):
    registry, directory, record = package(tmp_path)
    state = registry.read_state()
    state[pointer] = 'v1'
    registry.save_state(state)
    with pytest.raises(ValueError):
        registry.clean('v1', record['digest'], [], [])
    assert directory.exists()


def test_clean_and_overlap(tmp_path):
    registry, directory, record = package(tmp_path)
    with pytest.raises(ValueError):
        registry.clean('v1', record['digest'], [], [directory / 'archive'])
    registry.clean('v1', record['digest'], [], [])
    assert not directory.exists()
    assert registry.read_state()['versions'] == {}


def test_registry_fail_closed(tmp_path):
    registry, directory, record = package(tmp_path)
    (registry.state_dir / 'registry.json').write_text('{}')
    with pytest.raises(ValueError):
        registry.verify('v1', record['digest'])


def test_stale_digest_and_outside_root(tmp_path):
    registry, directory, record = package(tmp_path)
    with pytest.raises(ValueError):
        registry.verify('v1', '0' * 64)
    other = tmp_path / 'outside'
    other.mkdir()
    (other / 'a.exe').write_bytes(b'a')
    write_release(other, 'v2', 'backend')
    with pytest.raises(ValueError):
        registry.register(other)


def test_mutable_link_rejected(tmp_path):
    registry, directory, record = package(tmp_path)
    try:
        (directory / 'launcher.json').symlink_to(directory / 'QQDigest.exe')
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError):
        registry.verify('v1', record['digest'])


def test_pending_transaction_protected(tmp_path):
    registry, directory, record = package(tmp_path)
    (registry.state_dir / 'transaction.json').write_text(json.dumps({'status': 'prepared', 'target': record}))
    with pytest.raises(ValueError):
        registry.clean('v1', record['digest'], [], [])

@pytest.mark.parametrize('version', ['../escape', 'v/2', 'x' * 81, '版本', '.', '..'])
def test_invalid_version_names(tmp_path, version):
    directory = tmp_path / 'apps'
    directory.mkdir()
    (directory / 'a').write_bytes(b'a')
    with pytest.raises(ValueError):
        write_release(directory, version, 'backend')


def test_manifest_path_traversal_and_case_duplicates(tmp_path):
    registry, directory, record = package(tmp_path)
    manifest_path = directory / 'release.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['../outside'] = manifest['files']['QQDigest.exe']
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        registry.register(directory)
    manifest['files'].pop('../outside')
    manifest['files']['qqdigest.exe'] = manifest['files']['QQDigest.exe']
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        registry.register(directory)


def test_excluded_mutable_regular_files(tmp_path):
    registry, directory, record = package(tmp_path)
    (directory / 'launcher.json').write_text('{}')
    (directory / 'desktop-preferences.json').write_text('{}')
    assert registry.verify('v1', record['digest']) == record


def test_running_version_guard(tmp_path, monkeypatch):
    registry, directory, record = package(tmp_path)
    monkeypatch.setattr(registry, 'running_versions', lambda: {'v1'})
    with pytest.raises(ValueError):
        registry.clean('v1', record['digest'], [], [])


def test_windows_exclusive_occupancy_guard(tmp_path):
    import os
    if os.name != 'nt':
        pytest.skip('Windows exclusive handle test')
    import ctypes
    registry, directory, record = package(tmp_path)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(directory / 'QQDigest.exe'), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(ValueError):
            registry.clean('v1', record['digest'], [], [])
    finally:
        kernel.CloseHandle(handle)
    assert directory.exists()


def test_trusted_legacy_protocol_zero(tmp_path):
    registry, directory, record = package(tmp_path)
    other = registry.root / 'legacy'
    other.mkdir()
    (other / 'program.exe').write_bytes(b'legacy')
    write_release(other, 'legacy', 'backend', worker_protocol=0)
    record = registry.register(other)
    assert registry.verify('legacy', record['digest'])['worker_protocol'] == 0


def test_windows_junction_is_rejected(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction test')
    registry, directory, record = package(tmp_path)
    destination = tmp_path / 'user-data'
    destination.mkdir()
    junction = directory / 'linked-data'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(destination)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError):
            registry.verify('v1', record['digest'])
        with pytest.raises(ValueError):
            registry.clean('v1', record['digest'], [], [])
        assert destination.exists()
    finally:
        junction.rmdir()


def test_cleanup_protects_candidate_config(tmp_path):
    registry, directory, record = package(tmp_path)
    (directory / 'launcher.json').write_text(json.dumps({'config_path': 'config.yaml'}))
    with pytest.raises(ValueError):
        registry.clean('v1', record['digest'], [], [])
    assert directory.exists()


def test_cleanup_invalid_launcher_fails_closed(tmp_path):
    registry, directory, record = package(tmp_path)
    (directory / 'launcher.json').write_text('broken-json')
    with pytest.raises(ValueError):
        registry.clean('v1', record['digest'], [], [])
    assert directory.exists()


def test_cleanup_restores_after_quarantine_failure(tmp_path, monkeypatch):
    import qq_digest.desktop_releases as releases
    registry, directory, record = package(tmp_path)
    def fail(path):
        raise OSError('simulated removal failure')
    monkeypatch.setattr(releases.shutil, 'rmtree', fail)
    with pytest.raises(OSError):
        registry.clean('v1', record['digest'], [], [])
    assert directory.exists()
    assert registry.verify('v1', record['digest']) == record


def test_mutation_lease_rejects_other_registry_writer(tmp_path):
    registry, directory, record = package(tmp_path)
    other = ReleaseRegistry(registry.root, registry.state_dir)
    with registry.mutation():
        with pytest.raises(ValueError, match='其他操作'):
            other.register(directory)
        with pytest.raises(ValueError, match='其他操作'):
            other.clean('v1', record['digest'], [], [])
    assert other.register(directory) == record


def test_cleanup_holds_mutation_lease_through_removal(tmp_path, monkeypatch):
    import qq_digest.desktop_releases as releases
    registry, directory, record = package(tmp_path)
    other = ReleaseRegistry(registry.root, registry.state_dir)
    original = releases.shutil.rmtree
    observed = []
    def check_locked(path):
        with pytest.raises(ValueError, match='其他操作'):
            with other.mutation():
                pass
        observed.append(True)
        original(path)
    monkeypatch.setattr(releases.shutil, 'rmtree', check_locked)
    registry.clean('v1', record['digest'], [], [])
    assert observed == [True]
    with other.mutation():
        assert other.read_state()['versions'] == {}


def test_mutation_lease_blocks_separate_process(tmp_path):
    import subprocess
    import sys
    registry, directory, record = package(tmp_path)
    code = """
import sys
from qq_digest.desktop_releases import ReleaseRegistry
registry = ReleaseRegistry(sys.argv[1], sys.argv[2])
try:
    with registry.mutation():
        raise SystemExit(4)
except ValueError as exc:
    if '其他操作' not in str(exc):
        raise
"""
    with registry.mutation():
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(registry.root), str(registry.state_dir)], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode('utf-8')


def test_mutation_lease_releases_after_exception(tmp_path):
    registry, directory, record = package(tmp_path)
    with pytest.raises(RuntimeError):
        with registry.mutation():
            raise RuntimeError('failure')
    with registry.mutation():
        state = registry.read_state()
        state['current'] = 'v1'
        registry.save_state(state)
    assert registry.read_state()['current'] == 'v1'
