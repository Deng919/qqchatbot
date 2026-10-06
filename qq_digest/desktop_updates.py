"""Local program switches. User data always remains outside program bundles."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from .desktop_releases import ReleaseRegistry

STATE_DIR = Path(r'D:\Cache\QQDigestDesktop\updates')
ACTIVE = {'prepared', 'waiting', 'starting', 'committing', 'recovering'}


def atomic_json(path: Path, value: dict):
    from .desktop_releases import _atomic
    _atomic(path, value)


def signature(path: Path) -> str:
    path = path.resolve()
    return hashlib.sha256(str(path).casefold().encode() + b'\0' + path.read_bytes()).hexdigest()


def read_transaction(registry):
    from .desktop_releases import _safe
    path = registry.state_dir / 'transaction.json'
    _safe(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


class UpdateService:
    def __init__(self, registry: ReleaseRegistry, install_dir, config_path, backup,
                 *, data_paths):
        self.registry = registry
        self.install_dir = Path(install_dir).resolve()
        self.config_path = Path(config_path).resolve()
        self.backup = backup
        self.data_paths = list(data_paths)

    def current(self):
        for record in self.registry.read_state()['versions'].values():
            if Path(record['path']).resolve() == self.install_dir:
                return record
        raise ValueError('当前版本尚未登记，请重新打包桌面版')

    def snapshot(self):
        current = self.current()
        result = self.registry.snapshot(current['version'], self.data_paths)
        result['transaction'] = read_transaction(self.registry)
        # The API never exposes the startup token or entry snapshots.
        transaction = result['transaction']
        result['transaction'] = {key: transaction.get(key) for key in
                                 ('status', 'error', 'backup_path', 'source_version', 'target_version')}
        return result

    def prepare(self, version, digest, *, parent_pid, url):
        if read_transaction(self.registry).get('status') in ACTIVE:
            raise ValueError('已有版本切换尚未结束')
        source = self.current()
        source = self.registry.verify(source['version'], source['digest'])
        target = self.registry.verify(version, digest)
        if source['version'] == target['version']:
            raise ValueError('这已经是当前版本')
        if source['compatibility'] != target['compatibility']:
            raise ValueError('该版本的数据格式不兼容，不能直接切换')
        if source['worker_protocol'] < 1:
            raise ValueError('当前程序不支持版本切换，请先使用新版入口')
        if version in self.registry.running_versions():
            raise ValueError('目标版本正在运行，请先关闭它')
        for record in (source, target):
            directory = Path(record['path']).resolve()
            for data in self.data_paths:
                data = Path(data).resolve()
                if data == directory or data.is_relative_to(directory) or directory.is_relative_to(data):
                    raise ValueError('程序与数据目录重叠，不能切换')
        before = signature(self.config_path)
        backup = self.backup()
        if signature(self.config_path) != before:
            raise ValueError('配置在备份期间发生变化，请重新打开程序')
        self.registry.verify(version, digest)
        transaction = {
            'protocol': 1, 'status': 'prepared', 'token': secrets.token_hex(32),
            'source': source, 'target': target, 'source_version': source['version'],
            'target_version': target['version'], 'worker': source['version'],
            'parent_pid': parent_pid, 'url': url, 'config_path': str(self.config_path),
            'config_signature': before, 'backup_path': backup['path'],
            'target_launcher': _read_optional(Path(target['path']) / 'launcher.json'),
            'target_preferences': _read_optional(Path(target['path']) / 'desktop-preferences.json'),
            'source_preferences': _read_optional(self.install_dir / 'desktop-preferences.json'),
            'error': '',
        }
        atomic_json(self.registry.state_dir / 'transaction.json', transaction)
        return {'status': 'prepared', 'backup_path': backup['path']}


def _read_optional(path):
    return path.read_text(encoding='utf-8') if path.exists() else None


def _restore_optional(path, text):
    if text is None:
        path.unlink(missing_ok=True)
    else:
        # Mutable payloads are JSON; atomic writes also validate the saved content.
        atomic_json(path, json.loads(text))


def run_transaction(registry, runtime):
    """Runtime isolates Windows process/entry APIs for failure injection tests."""
    transaction = read_transaction(registry)
    if transaction.get('status') not in ACTIVE:
        return 0
    path = registry.state_dir / 'transaction.json'
    child = None
    parent_stopped = False
    entries_changed = False
    interrupted = transaction['status'] in {'starting', 'committing', 'recovering'}
    target_changed = interrupted
    source = transaction['source']
    target = transaction['target']
    # Only registry-owned records can be used for writes or process launch.
    state = registry.read_state()
    if (state['versions'].get(source.get('version')) != source or
            state['versions'].get(target.get('version')) != target):
        transaction.update(status='recovery_failed', error='切换记录与版本清单不一致，请使用原版本入口')
        atomic_json(path, transaction)
        return 1

    def save(status, **values):
        transaction.update(status=status, **values)
        atomic_json(path, transaction)

    try:
        save('waiting', worker_pid=os.getpid())
        if not runtime.wait_parent(transaction['parent_pid']):
            raise RuntimeError('原程序尚未退出，切换已取消')
        if interrupted and transaction.get('child_pid'):
            runtime.stop_existing(transaction['child_pid'], transaction.get('child_identity'), target)
        if not runtime.port_free(transaction['url']):
            # Another service still owns the old archive. Never start a second writer.
            parent_stopped = False
            raise RuntimeError('原服务仍在运行，切换已取消')
        parent_stopped = True
        registry.verify(source['version'], source['digest'])
        if interrupted:
            # A dead worker cannot resume an uncertain commit. Recover deterministically.
            runtime.switch_entries(transaction, source)
            raise RuntimeError('上次切换被中断，已退回原版本')
        registry.verify(target['version'], target['digest'])
        config_path = Path(transaction['config_path'])
        if signature(config_path) != transaction['config_signature']:
            raise RuntimeError('配置已变化，切换已取消')
        target_directory = Path(target['path'])
        save('starting')
        atomic_json(target_directory / 'launcher.json', {'config_path': str(config_path)})
        target_changed = True
        _restore_optional(target_directory / 'desktop-preferences.json', transaction['source_preferences'])
        child = runtime.launch(target, transaction)
        save('starting', child_pid=child,
             child_identity=runtime.identity(child) if hasattr(runtime, 'identity') else None)
        if not runtime.confirm(child, target, transaction):
            raise RuntimeError('新版未能启动，已退回原版本')
        save('committing')
        # This call must undo partial changes on exception.
        runtime.switch_entries(transaction, target)
        entries_changed = True
        with registry.mutation():
            state = registry.read_state()
            state.update(current=target['version'], rollback=source['version'])
            registry.save_state(state)
        save('success')
        return 0
    except Exception as exc:
        save('recovering', error=str(exc))
        if not parent_stopped:
            save('recovery_failed' if interrupted else 'canceled', error=str(exc))
            return 1
        try:
            if child is not None:
                runtime.stop(child)
            if entries_changed:
                runtime.switch_entries(transaction, source)
            if target_changed:
                _restore_optional(Path(target['path']) / 'launcher.json', transaction['target_launcher'])
                _restore_optional(Path(target['path']) / 'desktop-preferences.json', transaction['target_preferences'])
            with registry.mutation():
                state = registry.read_state()
                state['current'] = source['version']
                registry.save_state(state)
            if parent_stopped:
                registry.verify(source['version'], source['digest'])
                transaction['recovery_config_signature'] = signature(Path(transaction['config_path']))
                save('recovering')
                recovered = runtime.launch(source, transaction, recovery=True)
                if not runtime.confirm(recovered, source, transaction, recovery=True):
                    runtime.stop(recovered)
                    raise RuntimeError('原版本也未能启动')
            save('rolled_back' if parent_stopped else 'canceled')
        except Exception:
            save('recovery_failed', error='切换失败，自动回退未完成；请使用原版本入口。备份：' + transaction['backup_path'])
        return 1
