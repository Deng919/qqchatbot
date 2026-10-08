"""Storage inventory, retention previews and bounded file cleanup."""
from __future__ import annotations

import json
import copy
import os
import sqlite3
import threading
import uuid
import zipfile
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backup_restore import MAX_MANIFEST_BYTES, inspect_backup
from .storage_cleanup import CleanupError, fingerprint, isolate_delete, recover_isolation
from .storage_files import StorageBusy, activity_lock, cache_lock, cache_metadata, root_lock, safe_path
from .storage_inventory import inventory
from .storage_policy import StorageConflict, StoragePolicy


def utc_time(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None: raise ValueError('时间缺少时区')
    return stamp.astimezone(timezone.utc)


class StorageManager:
    def __init__(self, config, backup_root, temp_root, now=None):
        self.config = config
        safe_path(config.data_dir)
        safe_path(config.archive_path)
        self.backup_root = Path(backup_root).absolute()
        self.temp_root = Path(temp_root).absolute()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.policy = StoragePolicy(config.archive_path, now=self.now)
        self._previews = {}
        self._guard = threading.Lock()

    @property
    def owner(self): return self.config.data_dir.resolve()

    def snapshot(self):
        return {'inventory': inventory(self.config, self.backup_root, self.temp_root),
                'policy': self.policy.settings(), 'last_result': self.policy.last_result()}

    def update_policy(self, values, revision):
        with activity_lock(self.owner), root_lock(self.config.archive_path.parent):
            self.policy.update(values, revision)
            return self.snapshot()

    def _critical_paths(self):
        cfg = self.config
        paths = [cfg.archive_path, cfg.report_dir, cfg.knowledge_dir, cfg.log_dir,
                 cfg.data_dir / 'config', *cfg.resolve_knowledge_paths().values()]
        if cfg.ntqq.db_dir: paths.append(Path(cfg.ntqq.db_dir))
        for section, field in (('ai','api_key_file'), ('ai','ui_api_key_file'), ('security','session_secret_file')):
            value = getattr(getattr(cfg, section), field)
            if value:
                path = Path(value)
                paths.append(path if path.is_absolute() else cfg.data_dir / path)
        with closing(sqlite3.connect(f'{cfg.archive_path.as_uri()}?mode=ro', uri=True)) as db:
            for table in ('reports','manual_reports','knowledge_items'):
                columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                for column in ('markdown_path','json_path'):
                    if column in columns:
                        for row in db.execute(f'SELECT {column} FROM {table}'):
                            if row[0]:
                                path = Path(row[0])
                                paths.append(path if path.is_absolute() else cfg.data_dir / path)
        return [path.absolute() for path in paths]

    @staticmethod
    def _overlaps(path, critical):
        path = path.resolve()
        for protected in critical:
            protected = protected.resolve()
            if path == protected or path.is_relative_to(protected) or protected.is_relative_to(path):
                raise ValueError('与用户数据或来源目录重叠，已保留')

    def _backup_info(self, path):
        safe_path(path, self.backup_root)
        if path.lstat().st_nlink != 1: raise ValueError('链接文件已保留')
        if not path.name.startswith('QQDigest-automatic-') or path.suffix != '.zip':
            raise ValueError('手动备份或安全备份，已保留')
        with zipfile.ZipFile(path) as archive:
            meta = archive.getinfo('manifest.json')
            if meta.file_size > MAX_MANIFEST_BYTES: raise ValueError('备份清单过大')
            data = json.loads(archive.read(meta))
        if not isinstance(data, dict): raise ValueError('备份清单格式无效，已保留')
        if data.get('format_version') != 1 or data.get('kind') != 'automatic':
            raise ValueError('自动备份清单不匹配，已保留')
        source = data['source_data_dir']
        if not isinstance(source, str) or not Path(source).is_absolute() or '..' in Path(source).parts:
            raise ValueError('备份存储路径无效，已保留')
        if os.path.normcase(str(safe_path(source))) != os.path.normcase(str(self.owner)):
            raise ValueError('其他存储位置的备份，已保留')
        created = utc_time(data['created_at'])
        if created > self.now(): raise ValueError('备份时间异常，已保留')
        return created

    def _storage_identity(self):
        result = []
        for path in (self.owner, self.config.archive_path):
            mark = safe_path(path).lstat()
            result.append((mark.st_dev, mark.st_ino))
        return result

    def preview(self):
        with activity_lock(self.owner), root_lock(self.config.archive_path.parent), self._guard:
            return self._preview()

    def _preview(self):
        settings = self.policy.settings()
        candidates, protected, kept, busy = [], [], [], []
        critical = self._critical_paths()

        def protect(path, reason): protected.append({'path': str(path), 'reason': str(reason)})
        def candidate(path, root, kind, created):
            self._overlaps(path, critical)
            mark = fingerprint(path, root)
            return {'path': str(path), 'root': str(root), 'kind': kind,
                    'created_at': created.isoformat(), 'size_bytes': mark['size_bytes'], 'fingerprint': mark}

        if self.backup_root.exists():
            try:
                with root_lock(safe_path(self.backup_root)):
                    for warning in recover_isolation(self.backup_root, self.owner): protect(self.backup_root, warning)
                    valid = []
                    for path in sorted(self.backup_root.glob('QQDigest-*.zip')):
                        try:
                            self._overlaps(path, critical)
                            created = self._backup_info(path)
                            mark = fingerprint(path, self.backup_root)
                            inspect_backup(path, temp_root=self.temp_root)
                            if fingerprint(path, self.backup_root) != mark: raise ValueError('备份校验期间变化')
                            valid.append((created, path, mark))
                        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, StorageBusy) as exc:
                            protect(path, exc)
                            if isinstance(exc, StorageBusy): busy.append({'path': str(path), 'reason': str(exc)})
                    valid.sort(key=lambda row: (row[0], str(row[1])), reverse=True)
                    minimum = settings['backup_keep']
                    for index, (created, path, mark) in enumerate(valid):
                        if index < minimum or self.now() - created <= timedelta(days=settings['backup_days']):
                            kept.append({'path': str(path), 'fingerprint': mark})
                            protect(path, '最低保留份数' if index < minimum else '保留期限内')
                        else:
                            candidates.append(candidate(path, self.backup_root, 'backup', created))
            except (OSError, ValueError, StorageBusy) as exc:
                protect(self.backup_root, exc)
                if isinstance(exc, StorageBusy): busy.append({'path': str(self.backup_root), 'reason': str(exc)})

        for root in dict.fromkeys([self.temp_root, self.config.work_dir / 'snapshots']):
            if not root.exists(): continue
            try:
                with root_lock(safe_path(root)):
                    for warning in recover_isolation(root, self.owner): protect(root, warning)
                    for path in sorted(root.iterdir()):
                        if not path.name.startswith(('qqdigest-backup-', 'qqdigest-check-', 'refresh-')): continue
                        try:
                            safe_path(path, root)
                            with cache_lock(path):
                                meta = cache_metadata(path, self.owner)
                                created = utc_time(meta['created_at'])
                                if created > self.now(): raise ValueError('缓存时间异常，已保留')
                                if self.now() - created <= timedelta(days=settings['cache_days']):
                                    raise ValueError('保留期限内')
                                candidates.append(candidate(path, root, 'cache', created))
                        except (OSError, ValueError, StorageBusy) as exc:
                            protect(path, exc)
                            if isinstance(exc, StorageBusy): busy.append({'path': str(path), 'reason': str(exc)})
            except (OSError, ValueError, StorageBusy) as exc:
                protect(root, exc)
                if isinstance(exc, StorageBusy): busy.append({'path': str(root), 'reason': str(exc)})
        token = uuid.uuid4().hex
        expires = self.now() + timedelta(minutes=10)
        self._previews = {key: value for key,value in self._previews.items() if value['expires'] > self.now()}
        while len(self._previews) >= 16: self._previews.pop(next(iter(self._previews)))
        self._previews[token] = {'issued': self.now(), 'expires': expires, 'revision': settings['revision'],
                                 'candidates': candidates, 'kept': kept, 'busy': busy,
                                 'storage_identity': self._storage_identity()}
        return copy.deepcopy({'token': token, 'expires_at': expires.isoformat(), 'candidates': candidates,
                'protected': protected, 'estimated_bytes': sum(item['size_bytes'] for item in candidates)})

    def clean(self, token, automatic=False):
        with activity_lock(self.owner), root_lock(self.config.archive_path.parent), self._guard:
            preview = self._previews.pop(token, None)
            if preview is None: raise ValueError('预览已失效，请重新预览')
            if self.now() < preview['issued']: raise StorageConflict('系统时间已变化，请重新预览')
            if preview['expires'] <= self.now(): raise ValueError('预览已过期，请重新预览')
            if preview['storage_identity'] != self._storage_identity():
                raise StorageConflict('存储文件已切换，请重新预览')
            settings = self.policy.settings()
            if preview['revision'] != settings['revision']: raise StorageConflict('策略已变化，请重新预览')
            if automatic and not settings['enabled']: raise StorageConflict('自动清理已关闭')
            result = {'status': 'success', 'deleted_count': 0, 'deleted_bytes': 0,
                      'unknown_bytes': False, 'skipped': list(preview['busy']), 'errors': []}
            for item in preview['candidates']:
                path, root = Path(item['path']), Path(item['root'])
                try:
                    with root_lock(root):
                        with cache_lock(path) if item['kind'] == 'cache' else nullcontext():
                            self._overlaps(path, self._critical_paths())
                            if item['kind'] == 'backup':
                                for kept in preview['kept']:
                                    if fingerprint(Path(kept['path']), root) != kept['fingerprint']:
                                        raise ValueError('保留备份已变化，本轮不删除备份')
                                created = self._backup_info(path)
                                days = settings['backup_days']
                            else:
                                created = utc_time(cache_metadata(path, self.owner)['created_at'])
                                days = settings['cache_days']
                            if self.now() - created <= timedelta(days=days):
                                raise ValueError('项目仍在保留期限内，已跳过')
                            isolate_delete(item, self.owner)
                    result['deleted_count'] += 1
                    result['deleted_bytes'] += item['size_bytes']
                except CleanupError as exc:
                    if exc.removed_bytes is None: result['unknown_bytes'] = True
                    else: result['deleted_bytes'] += exc.removed_bytes
                    result['errors'].append({'path': str(path), 'reason': str(exc)})
                except (ValueError, StorageBusy, FileNotFoundError) as exc:
                    result['skipped'].append({'path': str(path), 'reason': str(exc)})
                except OSError as exc: result['errors'].append({'path': str(path), 'reason': str(exc)})
            if result['errors'] or result['skipped']: result['status'] = 'partial'
            self.policy.record(result, automatic)
            return result

    def maybe_automatic(self):
        with activity_lock(self.owner), root_lock(self.config.archive_path.parent):
            if not self.policy.automatic_due(self.now()): return None
            preview = self.preview()
            if self._previews[preview['token']]['busy'] and not preview['candidates']:
                raise StorageBusy('文件正在使用，稍后重试')
            return self.clean(preview['token'], automatic=True)
