"""Synthetic-directory coverage for the storage cleanup safety boundary."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


def storage():
    return importlib.import_module("qq_digest.storage_files")


def child(script, *args):
    return subprocess.run([sys.executable, "-X", "utf8", "-c", script, *map(str, args)],
                          capture_output=True, text=True, timeout=15)


def test_managed_temporary_records_owner_and_cleans_up(tmp_path):
    files = storage()
    owner = tmp_path / "data"
    with files.managed_temporary(tmp_path / "cache", "refresh-", owner) as path:
        assert isinstance(path, Path) and path.is_dir()
        metadata = files.cache_metadata(path, owner)
        assert metadata["version"] == 1
        assert metadata["owner"] == os.path.normcase(os.path.abspath(owner))
        assert metadata["prefix"] == "refresh-"
        assert metadata["created_at"].endswith("+00:00")
        with pytest.raises(files.StorageBusy):
            with files.cache_lock(path):
                pass
    assert not path.exists()


def test_managed_temporary_cleans_on_exception(tmp_path):
    files = storage()
    with pytest.raises(RuntimeError, match="synthetic"):
        with files.managed_temporary(tmp_path / "cache", "qqdigest-check-", tmp_path / "data") as path:
            raise RuntimeError("synthetic")
    assert not path.exists()


def test_managed_temporary_retries_transient_permission_error(tmp_path, monkeypatch):
    files = storage()
    original = files.shutil.rmtree
    calls = 0

    def flaky_rmtree(path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, "Access is denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(files.shutil, "rmtree", flaky_rmtree)
    with files.managed_temporary(tmp_path / "cache", "refresh-", tmp_path / "data") as path:
        assert path.is_dir()
    assert calls == 2
    assert not path.exists()


@pytest.mark.parametrize("prefix", ["anything-", "qqdigest-restore-", "../refresh-"])
def test_managed_temporary_rejects_unknown_prefix(tmp_path, prefix):
    with pytest.raises(ValueError):
        with storage().managed_temporary(tmp_path / "cache", prefix, tmp_path / "data"):
            pass
    assert not (tmp_path / "cache").exists()


def test_cache_marker_missing_or_wrong_owner_is_protected(tmp_path):
    files = storage()
    path = tmp_path / "refresh-old"
    path.mkdir()
    with pytest.raises(ValueError):
        files.cache_metadata(path, tmp_path / "data")
    with files.managed_temporary(tmp_path, "refresh-", tmp_path / "data") as path:
        with pytest.raises(ValueError):
            files.cache_metadata(path, tmp_path / "other")


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"prefix": "qqdigest-check-"},
    {"created_at": "yesterday"}, {"created_at": "2026-10-08T00:00:00"},
    {"owner": "relative"}, {"owner": None}, {"owner": 42}, {"prefix": []},
    {"created_at": []}, {"unexpected": "field"},
])
def test_cache_marker_strictly_validates(tmp_path, change):
    files = storage()
    with files.managed_temporary(tmp_path, "refresh-", tmp_path / "data") as path:
        marker = path / ".qqdigest-cache.json"
        value = json.loads(marker.read_text(encoding="utf-8"))
        value.update(change)
        marker.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            files.cache_metadata(path, tmp_path / "data")


def test_cache_lock_allows_quarantine_rename_and_rejects_another_process(tmp_path):
    files = storage()
    path = tmp_path / "refresh-abandoned"
    path.mkdir()
    with files.cache_lock(path):
        result = child("from pathlib import Path; import sys; from qq_digest.storage_files import cache_lock, StorageBusy\ntry:\n with cache_lock(Path(sys.argv[1])): pass\nexcept StorageBusy: sys.exit(23)", path)
        assert result.returncode == 23, result.stderr
        quarantine = tmp_path / "quarantine"
        path.rename(quarantine)
        assert quarantine.is_dir()
    with files.cache_lock(path):
        pass


def test_cache_marker_rejects_noncanonical_absolute_owner(tmp_path):
    files = storage()
    owner = tmp_path / "data"
    with files.managed_temporary(tmp_path, "refresh-", owner) as path:
        marker = path / ".qqdigest-cache.json"
        value = json.loads(marker.read_text(encoding="utf-8"))
        value["owner"] = str(owner / ".." / "data")
        marker.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            files.cache_metadata(path, owner)


def test_cache_marker_rejects_duplicate_json_fields(tmp_path):
    files = storage()
    with files.managed_temporary(tmp_path, "refresh-", tmp_path / "data") as path:
        marker = path / ".qqdigest-cache.json"
        content = marker.read_text(encoding="utf-8")
        marker.write_text('{"version": 2, ' + content[1:], encoding="utf-8")
        with pytest.raises(ValueError):
            files.cache_metadata(path, tmp_path / "data")


def test_root_lock_is_reentrant_and_blocks_another_process(tmp_path):
    files = storage()
    with files.root_lock(tmp_path):
        with files.root_lock(tmp_path):
            result = child("from pathlib import Path; import sys; from qq_digest.storage_files import root_lock, StorageBusy\ntry:\n with root_lock(Path(sys.argv[1])): pass\nexcept StorageBusy: sys.exit(23)", tmp_path)
            assert result.returncode == 23, result.stderr
    with files.root_lock(tmp_path):
        pass
    assert [path.name for path in tmp_path.iterdir()] == [".qqdigest-storage.lock"]


def test_root_lock_serializes_threads(tmp_path):
    files = storage()
    started = threading.Event()
    entered = threading.Event()
    failures = []
    def work():
        started.set()
        try:
            with files.root_lock(tmp_path):
                entered.set()
        except Exception as exc:
            failures.append(exc)
    with files.root_lock(tmp_path):
        worker = threading.Thread(target=work)
        worker.start()
        assert started.wait(2)
        assert not entered.wait(0.1)
    worker.join(3)
    assert entered.is_set() and not failures


def test_crashed_managed_temporary_is_recognized_and_unlocked(tmp_path):
    result = child("from pathlib import Path; import sys, os; from qq_digest.storage_files import managed_temporary\nwith managed_temporary(Path(sys.argv[1]), 'refresh-', Path(sys.argv[2])) as path:\n print(path, flush=True)\n os._exit(0)", tmp_path / "cache", tmp_path / "data")
    assert result.returncode == 0, result.stderr
    path = Path(result.stdout.strip())
    assert storage().cache_metadata(path, tmp_path / "data")["prefix"] == "refresh-"
    with storage().cache_lock(path):
        pass


def test_safe_path_rejects_outside_root_and_returns_absolute_lexical_path(tmp_path):
    files = storage()
    assert files.safe_path(tmp_path / "new", tmp_path) == tmp_path / "new"
    with pytest.raises(ValueError):
        files.safe_path(tmp_path.parent / "outside", tmp_path)


def test_safe_path_rejects_windows_junction_ancestor(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError):
            storage().safe_path(link / "nested" / "new")
    finally:
        link.rmdir()


def synthetic_backup_config(tmp_path):
    from qq_digest.archive import Archive
    root = tmp_path / "data"
    config = root / "config" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("security:\n  web_password_hash: test\nai:\n  model: test\n  base_url: https://example.com\n  api_key_env: TEST_KEY\n", encoding="utf-8")
    Archive.open(root / "archive" / "archive.sqlite").connection.close()
    return config


@pytest.mark.parametrize("operation,prefix", [("backup", "qqdigest-backup-"), ("inspect", "qqdigest-check-")])
def test_backup_operations_hold_root_lock_and_managed_cache(tmp_path, monkeypatch, operation, prefix):
    import qq_digest.backup_restore as module
    import zipfile
    files = storage()
    config = synthetic_backup_config(tmp_path)
    destination = tmp_path / "backups"
    cache = tmp_path / "cache"
    backup = Path(module.backup_data(config, destination, temp_root=cache)["path"]) if operation == "inspect" else None
    observations = []
    def observe():
        temporary, = cache.glob(prefix + "*")
        assert files.cache_metadata(temporary, config.parent.parent)["prefix"] == prefix
        with pytest.raises(files.StorageBusy):
            with files.cache_lock(temporary):
                pass
        result = child("from pathlib import Path; import sys; from qq_digest.storage_files import root_lock, StorageBusy\ntry:\n with root_lock(Path(sys.argv[1])): pass\nexcept StorageBusy: sys.exit(23)", destination)
        assert result.returncode == 23, result.stderr
        observations.append(temporary)
    if operation == "backup":
        original_write = zipfile.ZipFile.write
        def write(self, *args, **kwargs):
            if not observations:
                observe()
            return original_write(self, *args, **kwargs)
        monkeypatch.setattr(zipfile.ZipFile, "write", write)
        module.backup_data(config, destination, temp_root=cache)
    else:
        original_validate = module._validate_core_schema
        def validate(db):
            observe()
            return original_validate(db)
        monkeypatch.setattr(module, "_validate_core_schema", validate)
        with files.root_lock(destination):
            assert module.inspect_backup(backup, temp_root=cache)["valid"]
    assert observations and all(not path.exists() for path in observations)


def activity_child(root):
    return child("from pathlib import Path; import sys; from qq_digest.storage_files import activity_lock, StorageBusy\ntry:\n with activity_lock(Path(sys.argv[1])): pass\nexcept StorageBusy: sys.exit(23)", root)


def test_activity_lock_reentrant_and_blocks_another_process(tmp_path):
    files = storage()
    with files.activity_lock(tmp_path):
        with files.activity_lock(tmp_path):
            result = activity_child(tmp_path)
            assert result.returncode == 23, result.stderr
        assert activity_child(tmp_path).returncode == 23
    result = activity_child(tmp_path)
    assert result.returncode == 0, result.stderr
    assert [path.name for path in tmp_path.iterdir()] == [".qqdigest-activity.lock"]


def test_activity_lock_rejects_another_thread_immediately(tmp_path):
    files = storage()
    outcome = []
    def claim():
        try:
            with files.activity_lock(tmp_path):
                outcome.append("acquired")
        except files.StorageBusy:
            outcome.append("busy")
    with files.activity_lock(tmp_path):
        worker = threading.Thread(target=claim)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert outcome == ["busy"]


def test_activity_lock_can_release_from_watcher_thread(tmp_path):
    files = storage()
    lease = files.activity_lock(tmp_path)
    lease.__enter__()
    failures = []
    def release():
        try:
            lease.__exit__(None, None, None)
        except BaseException as exc:
            failures.append(exc)
    worker = threading.Thread(target=release)
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and not failures
    result = activity_child(tmp_path)
    assert result.returncode == 0, result.stderr
    with files.activity_lock(tmp_path):
        pass


def test_activity_lock_retains_nested_lease_when_outer_released_elsewhere(tmp_path):
    files = storage()
    outer = files.activity_lock(tmp_path)
    inner = files.activity_lock(tmp_path)
    outer.__enter__()
    inner.__enter__()
    worker = threading.Thread(target=lambda: outer.__exit__(None, None, None))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert activity_child(tmp_path).returncode == 23
    inner.__exit__(None, None, None)
    assert activity_child(tmp_path).returncode == 0


def test_activity_lock_does_not_create_missing_root(tmp_path):
    root = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        with storage().activity_lock(root):
            pass
    assert not root.exists()


def test_activity_lock_does_not_treat_reused_thread_id_as_reentry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    files = storage()
    # A transferred lease can outlive its originating thread; OS thread IDs
    # may then be reused. Keep thread identity distinct even under that reuse.
    monkeypatch.setattr(files, "threading", SimpleNamespace(
        get_ident=lambda: 7, current_thread=threading.current_thread))
    outcome = []
    def claim():
        try:
            with files.activity_lock(tmp_path):
                outcome.append("acquired")
        except files.StorageBusy:
            outcome.append("busy")
    with files.activity_lock(tmp_path):
        worker = threading.Thread(target=claim)
        worker.start()
        worker.join(2)
        assert outcome == ["busy"]
