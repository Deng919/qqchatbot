"""Fail-closed local desktop release inventory; hashes check integrity, not trust."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager

PRODUCT = 'qq-digest-desktop'
MUTABLE = {'release.json', 'launcher.json', 'desktop-preferences.json'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or value in {'.', '..'}:
        raise ValueError('版本标识无效')
    return value


def _safe(path):
    path = Path(os.path.abspath(path))
    for component in [*reversed(path.parents), path]:
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('版本路径包含链接或重解析点，无法操作')
    return path


def _atomic(path, value):
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path):
    path = _safe(path)
    if not path.is_file():
        raise ValueError('状态文件必须是普通文件')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('状态文件格式无效') from exc


def _files(directory):
    result = {}
    seen = set()
    for base, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            path = _safe(Path(base) / name)
            relative = path.relative_to(directory).as_posix()
            folded = relative.casefold()
            if folded in seen:
                raise ValueError('程序清单包含大小写重复路径')
            seen.add(folded)
            if relative in MUTABLE and name in dirs:
                raise ValueError('启动配置和偏好必须是普通文件')
            if name in files:
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError('程序包包含非普通文件')
                if relative in MUTABLE:
                    continue
                result[relative] = {'sha256': _hash_file(path), 'size_bytes': path.stat().st_size}
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_release(directory, version, backend_id, compatibility='archive-2026-10-06', worker_protocol=1):
    directory = _safe(directory)
    if not directory.is_dir():
        raise ValueError('版本目录不存在')
    _name(version)
    if not isinstance(backend_id, str) or not backend_id or not isinstance(compatibility, str) or not compatibility or type(worker_protocol) is not int or worker_protocol < 0:
        raise ValueError('版本信息格式无效')
    from datetime import datetime, timezone
    manifest = dict(product=PRODUCT, version=version, backend_id=backend_id, compatibility=compatibility, worker_protocol=worker_protocol, built_at=datetime.now(timezone.utc).isoformat(), files=_files(directory))
    if not manifest['files']:
        raise ValueError('程序包为空')
    _atomic(directory / 'release.json', manifest)
    return manifest


def _windows_process_paths():
    """Query process executable paths using Windows APIs, without optional packages."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    capacity = 1024
    while True:
        identifiers = (wintypes.DWORD * capacity)()
        used = wintypes.DWORD()
        if not psapi.EnumProcesses(identifiers, ctypes.sizeof(identifiers), ctypes.byref(used)):
            raise ValueError('无法核对正在运行的程序')
        if used.value < ctypes.sizeof(identifiers):
            break
        capacity *= 2
    paths = []
    for pid in identifiers[:used.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            continue  # System/protected processes do not expose user release paths.
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                paths.append(Path(buffer.value))
        finally:
            kernel.CloseHandle(handle)
    return paths


class ReleaseRegistry:
    def __init__(self, root, state_dir):
        self.root = _safe(root)
        self.state_dir = _safe(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.state_dir / 'registry.json'

    @contextmanager
    def mutation(self):
        """Hold a fail-fast process lease across a registry read/modify/commit."""
        path = _safe(self.state_dir / 'registry.lock')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags, 0o600)
        locked = False
        try:
            _safe(path)
            opened = os.fstat(descriptor)
            current = path.lstat()
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError('版本管理锁文件无效')
            if opened.st_size == 0:
                os.write(descriptor, b'\0')
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise ValueError('版本管理正在执行其他操作，请稍后重试') from exc
            yield
        finally:
            try:
                if locked:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == 'nt':
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def read_state(self):
        _safe(self.registry_path)
        state = _read(self.registry_path) if self.registry_path.exists() else {'versions': {}, 'current': None, 'rollback': None}
        if not isinstance(state, dict) or not isinstance(state.get('versions'), dict) or not all(key in state for key in ('current', 'rollback')):
            raise ValueError('版本登记状态无效')
        for version, record in state['versions'].items():
            _name(version)
            if not isinstance(record, dict) or record.get('version') != version or not all(key in record for key in ('path', 'backend_id', 'compatibility', 'worker_protocol', 'digest', 'size_bytes')):
                raise ValueError('版本登记记录无效')
            self._validate_record(record)
            self._directory(record['path'])
        for key in ('current', 'rollback'):
            if state[key] is not None and state[key] not in state['versions']:
                raise ValueError('当前或回退版本未登记')
        return state

    def save_state(self, state):
        # Validate before replacement using the same schema, without ever exposing partial state.
        self.read_state()
        if not isinstance(state, dict) or not isinstance(state.get('versions'), dict) or not all(key in state for key in ('current', 'rollback')):
            raise ValueError('版本登记状态无效')
        for version, record in state['versions'].items():
            _name(version)
            if not isinstance(record, dict) or record.get('version') != version or not all(key in record for key in ('path', 'backend_id', 'compatibility', 'worker_protocol', 'digest', 'size_bytes')):
                raise ValueError('版本登记记录无效')
            self._validate_record(record)
            self._directory(record['path'])
        for key in ('current', 'rollback'):
            if state[key] is not None and state[key] not in state['versions']:
                raise ValueError('当前或回退版本未登记')
        _atomic(self.registry_path, state)

    @staticmethod
    def _validate_record(record):
        if (not isinstance(record['path'], str) or not Path(record['path']).is_absolute()
                or not isinstance(record['backend_id'], str) or not record['backend_id']
                or not isinstance(record['compatibility'], str) or not record['compatibility']
                or type(record['worker_protocol']) is not int or record['worker_protocol'] < 0
                or not isinstance(record['digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', record['digest'])
                or type(record['size_bytes']) is not int or record['size_bytes'] < 0):
            raise ValueError('版本登记记录无效')

    def _directory(self, path):
        path = _safe(path)
        if path == self.root or not path.is_relative_to(self.root):
            raise ValueError('版本目录不在受管理目录内')
        return path

    def _verify_directory(self, directory):
        directory = self._directory(directory)
        if not directory.is_dir():
            raise ValueError('版本目录不存在')
        manifest = _read(directory / 'release.json')
        if not isinstance(manifest, dict) or manifest.get('product') != PRODUCT:
            raise ValueError('程序包清单的产品标识无效')
        _name(manifest.get('version'))
        if not isinstance(manifest.get('backend_id'), str) or not manifest['backend_id'] or not isinstance(manifest.get('compatibility'), str) or not manifest['compatibility'] or type(manifest.get('worker_protocol')) is not int or manifest['worker_protocol'] < 0:
            raise ValueError('版本信息格式无效')
        actual = _files(directory)
        if not actual or actual != manifest.get('files'):
            raise ValueError('程序文件已修改、缺失或增加，请重新核对版本')
        return dict(version=manifest['version'], path=str(directory), backend_id=manifest['backend_id'], compatibility=manifest['compatibility'], worker_protocol=manifest['worker_protocol'], digest=_hash_file(directory / 'release.json'), size_bytes=sum(file['size_bytes'] for file in actual.values()))

    def register(self, directory):
        with self.mutation():
            return self._register(directory)

    def _register(self, directory):
        record = self._verify_directory(directory)
        state = self.read_state()
        previous = state['versions'].get(record['version'])
        if previous is not None and previous != record:
            raise ValueError('该版本标识已登记为另一程序包')
        if previous == record:
            return record
        state['versions'][record['version']] = record
        self.save_state(state)
        return record

    def verify(self, version, expected_digest):
        _name(version)
        record = self.read_state()['versions'].get(version)
        if record is None:
            raise ValueError('该版本尚未登记')
        actual = self._verify_directory(record['path'])
        if actual != record or actual['digest'] != expected_digest:
            raise ValueError('版本清单已变化，请刷新后重试')
        return actual

    def running_versions(self):
        paths = [Path(sys.executable)]
        if os.name == 'nt':
            paths.extend(_windows_process_paths())
        return {version for version, record in self.read_state()['versions'].items() if any(Path(os.path.abspath(path)).is_relative_to(Path(record['path'])) for path in paths)}

    def snapshot(self, current=None, data_paths=()):
        state = self.read_state()
        running = self.running_versions()
        versions = []
        for version, record in state['versions'].items():
            item = dict(record)
            reasons = []
            valid = True
            try:
                self.verify(version, record['digest'])
            except ValueError as exc:
                valid = False
                reasons.append(str(exc))
            if version in {current, state['current'], state['rollback']}:
                reasons.append('当前版本或回退版本需要保留')
            if version in running:
                reasons.append('版本正在运行')
            try:
                if self._overlap(record['path'], self._cleanup_data_paths(record, data_paths)):
                    reasons.append('版本目录与用户数据路径重叠')
            except ValueError as exc:
                reasons.append(str(exc))
            if version in self._pending_protected():
                reasons.append('更新事务正在使用该版本')
            item.update(cleanable=not reasons, reasons=reasons, valid=valid)
            versions.append(item)
        return dict(versions=versions, current=current or state['current'], rollback=state['rollback'])

    @staticmethod
    def _overlap(directory, data_paths):
        directory = _safe(directory)
        return any(directory.is_relative_to(path) or path.is_relative_to(directory) for path in map(_safe, data_paths))

    def _cleanup_data_paths(self, record, data_paths):
        data_paths = list(data_paths) + [self.state_dir]
        launcher_path = Path(record['path']) / 'launcher.json'
        if launcher_path.exists():
            launcher = _read(launcher_path)
            if not isinstance(launcher, dict):
                raise ValueError('启动配置格式无效，无法清理')
            config_path = launcher.get('config_path')
            if config_path is not None:
                if not isinstance(config_path, str) or not config_path:
                    raise ValueError('启动配置路径无效，无法清理')
                config_path = Path(config_path)
                if not config_path.is_absolute():
                    config_path = launcher_path.parent / config_path
                data_paths.append(config_path)
        return data_paths

    def _pending_protected(self):
        protected = set()
        transaction_path = self.state_dir / 'transaction.json'
        if transaction_path.exists():
            transaction = _read(transaction_path)
            if not isinstance(transaction, dict):
                raise ValueError('更新事务状态无效')
            if transaction.get('status') not in {'committed', 'rolled_back', 'failed', 'complete', 'success', 'canceled'}:
                for key in ('source', 'target', 'worker'):
                    value = transaction.get(key)
                    protected.add(value.get('version') if isinstance(value, dict) else value if isinstance(value, str) else None)
        return protected

    def clean(self, version, expected_digest, protected=(), data_paths=()):
        with self.mutation():
            return self._clean(version, expected_digest, protected, data_paths)

    def _clean(self, version, expected_digest, protected=(), data_paths=()):
        record = self.verify(version, expected_digest)
        state = self.read_state()
        data_paths = self._cleanup_data_paths(record, data_paths)
        protected = set(protected) | {state['current'], state['rollback']} | self.running_versions() | self._pending_protected()
        if version in protected or self._overlap(record['path'], data_paths):
            raise ValueError('该版本需要保留，无法清理')
        directory = self._directory(record['path'])
        # Windows exclusive opens detect occupied DLLs and binaries without stopping processes.
        handles = []
        try:
            if os.name == 'nt':
                import ctypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
                kernel.CreateFileW.restype = ctypes.c_void_p
                for path in directory.rglob('*'):
                    _safe(path)
                    if path.is_file():
                        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
                        if handle == ctypes.c_void_p(-1).value:
                            raise ValueError('程序文件正在使用，无法清理')
                        handles.append(handle)
        finally:
            if handles:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                for handle in handles:
                    kernel.CloseHandle(handle)
        self.verify(version, expected_digest)
        _safe(directory)
        # Move the checked tree to an unpredictable sibling before deleting it.
        # A failed verification restores the registered path for a later retry.
        import uuid
        quarantine = self.root / ('.cleanup-' + uuid.uuid4().hex)
        directory.rename(quarantine)
        try:
            _safe(quarantine)
            actual = self._verify_directory(quarantine)
            if actual != dict(record, path=str(quarantine)):
                raise ValueError('版本在清理过程中发生变化')
            if self._overlap(quarantine, self._cleanup_data_paths(actual, data_paths)):
                raise ValueError('版本目录与用户数据路径重叠')
            latest = self.read_state()
            if version in {latest['current'], latest['rollback']} | self._pending_protected():
                raise ValueError('该版本需要保留，无法清理')
            if latest['versions'].get(version) != record:
                raise ValueError('版本登记在清理过程中发生变化')
            state = latest
            if os.name == 'nt' and any(path.is_relative_to(quarantine) or path.is_relative_to(directory) for path in _windows_process_paths()):
                raise ValueError('版本正在运行，无法清理')
            _safe(quarantine)
            shutil.rmtree(quarantine)
        except BaseException:
            if quarantine.exists() and not directory.exists():
                _safe(quarantine)
                quarantine.rename(directory)
            raise
        del state['versions'][version]
        self.save_state(state)
        return {'version': version, 'removed': True}
