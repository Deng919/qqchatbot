"""Read-only logical-file inventory of bounded QQDigest-owned locations."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import stat


CATEGORIES = (('database', '数据库'), ('reports', '摘要文件'), ('knowledge', '知识文件'),
              ('cache', '程序缓存'), ('backups', '备份'), ('logs', '日志'))
REPARSE_POINT = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def _absolute(path):
    # Do not resolve links: their lexical ancestry must remain visible to lstat.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _inside(path, root):
    return path == root or path.is_relative_to(root)


class _Inventory:
    def __init__(self, config, backup_root, temp_root):
        self.config = config
        self.warnings = []
        self.blocked = set()
        self.current_category = None
        self.rows = {name: {'name': name, 'label': label, 'size_bytes': 0, 'file_count': 0, 'complete': True}
                     for name, label in CATEGORIES}
        self.storage = self.path(config.data_dir)
        self.backups = self.path(backup_root)
        self.cache = self.path(temp_root)
        self.seen = set()
        source = getattr(getattr(config, 'ntqq', None), 'db_dir', '')
        self.sources = [self.path(source)] if source else []
        # These are external originals, even if a configured category includes them.
        self.sources.append(self.path(Path.home() / 'Documents' / 'Tencent Files'))

    def warning(self, message, incomplete=False):
        if incomplete and self.current_category is not None:
            self.rows[self.current_category]['complete'] = False
        if message not in self.warnings:
            self.warnings.append(message)

    def path(self, value):
        lexical = Path(os.path.expanduser(str(value)))
        if not lexical.is_absolute():
            lexical = Path.cwd() / lexical
        normalized = _absolute(lexical)
        # Check before normalization so junction/../file cannot hide a link.
        for ancestor in reversed((lexical, *lexical.parents)):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                self.warning(f'无法检查路径 {ancestor}：{exc}', incomplete=True)
                self.blocked.add(normalized)
                break
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & REPARSE_POINT:
                self.warning(f'已跳过链接或重解析点 {ancestor}，统计不完整', incomplete=True)
                self.blocked.add(normalized)
                break
        return normalized

    def safe(self, path, required=False):
        """Reject every link/reparse ancestor, including junctions on Windows."""
        if any(_inside(path, blocked) for blocked in self.blocked):
            if self.current_category is not None:
                self.rows[self.current_category]['complete'] = False
            return False
        for ancestor in reversed((path, *path.parents)):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                if required:
                    self.warning(f'扫描期间文件发生变化 {path}，统计不完整', incomplete=True)
                return False
            except OSError as exc:
                self.warning(f'无法检查路径 {ancestor}：{exc}', incomplete=True)
                return False
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & REPARSE_POINT:
                self.warning(f'已跳过链接或重解析点 {ancestor}，统计不完整', incomplete=True)
                return False
        return True

    def excluded(self, path):
        if any(_inside(path, source) for source in self.sources):
            self.warning(f'已排除 QQ 原始来源目录 {path}')
            return True
        return False

    def add(self, path, category):
        if self.excluded(path) or not self.safe(path):
            return
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return
            identity = (info.st_dev, info.st_ino) if info.st_ino else ('path', str(path).casefold())
            if identity in self.seen:
                return
            self.seen.add(identity)
            self.rows[category]['size_bytes'] += info.st_size
            self.rows[category]['file_count'] += 1
        except OSError as exc:
            self.warning(f'无法统计文件 {path}：{exc}', incomplete=True)

    def walk(self, root, category):
        if self.excluded(root) or not self.safe(root):
            return
        try:
            if root.is_file():
                self.add(root, category)
                return
            with os.scandir(root) as entries:
                children = [Path(entry.path) for entry in entries]
        except OSError as exc:
            self.warning(f'无法扫描目录 {root}：{exc}', incomplete=True)
            return
        for child in children:
            if self.excluded(child) or not self.safe(child, required=True):
                continue
            try:
                info = child.lstat()
            except OSError as exc:
                self.warning(f'扫描期间文件发生变化 {child}：{exc}', incomplete=True)
                continue
            if stat.S_ISDIR(info.st_mode):
                self.walk(child, category)
            elif stat.S_ISREG(info.st_mode):
                self.add(child, category)

    def broad(self, root):
        home = _absolute(Path.home())
        shared_names = {'users', 'documents and settings', 'documents', 'desktop',
                        'downloads', 'pictures', 'videos', 'music', 'appdata',
                        'program files', 'program files (x86)', 'programdata', 'windows'}
        return (root == Path(root.anchor) or _inside(self.storage, root)
                or _inside(home, root) or root.name.casefold() in shared_names
                or any(_inside(source, root) for source in self.sources))

    def references(self):
        result = {'reports': [], 'knowledge': []}
        path = self.path(self.config.archive_path)
        if self.excluded(path) or not self.safe(path):
            return result
        try:
            wal = Path(str(path) + '-wal')
            if self.safe(wal) and wal.stat().st_size:
                self.warning('归档存在 WAL，共享目录的已知文件清单可能尚未合并，统计不完整')
            # immutable prevents SQLite from creating SHM/journal files during inspection.
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                for table, category, columns in (
                    ('reports', 'reports', ('markdown_path', 'json_path')),
                    ('manual_reports', 'reports', ('markdown_path', 'json_path')),
                    ('knowledge_items', 'knowledge', ('markdown_path',)),
                ):
                    existing = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                    selected = [column for column in columns if column in existing]
                    if not selected:
                        continue
                    for row in db.execute(f'SELECT {",".join(selected)} FROM {table}'):
                        for value in row:
                            if isinstance(value, str) and value:
                                item = Path(value).expanduser()
                                result[category].append(self.path(item if item.is_absolute() else self.storage / item))
        except (OSError, sqlite3.Error) as exc:
            self.warning(f'无法读取归档文件清单 {path}：{exc}')
        return result

    def known_children(self, root, prefixes, category, files=False):
        if self.excluded(root) or not self.safe(root):
            return
        try:
            with os.scandir(root) as entries:
                children = [Path(entry.path) for entry in entries]
        except OSError as exc:
            self.warning(f'无法读取受管目录 {root}：{exc}', incomplete=True)
            return
        for child in children:
            if not any(child.name.startswith(prefix) for prefix in prefixes):
                continue
            if files:
                if child.suffix.casefold() in ('.zip', '.partial'):
                    self.add(child, category)
            elif self.safe(child, required=True):
                try:
                    if stat.S_ISDIR(child.lstat().st_mode):
                        self.walk(child, category)
                except OSError as exc:
                    self.warning(f'无法检查缓存目录 {child}：{exc}', incomplete=True)

    def disks(self, roots):
        disks, devices = [], set()
        for original in roots:
            nearest = original
            while not nearest.exists() and nearest != nearest.parent:
                nearest = nearest.parent
            if not self.safe(nearest):
                continue
            try:
                info = nearest.stat()
                identity = nearest.drive.casefold() if nearest.drive else info.st_dev
                if identity in devices:
                    continue
                devices.add(identity)
                disks.append({'path': nearest.anchor or str(nearest), 'free_bytes': shutil.disk_usage(nearest).free})
            except OSError as exc:
                self.warning(f'无法读取磁盘剩余空间 {nearest}：{exc}')
                disks.append({'path': nearest.anchor or str(nearest), 'free_bytes': None})
        return disks

    def result(self):
        self.current_category = 'database'
        archive = self.path(self.config.archive_path)
        for suffix in ('', '-wal', '-shm'):
            self.add(Path(str(archive) + suffix), 'database')
        self.current_category = None
        refs = self.references()
        roots = [self.storage, self.backups, self.cache, archive.parent]
        for category, attr in (('reports', 'report_dir'), ('knowledge', 'knowledge_dir')):
            self.current_category = category
            root = self.path(getattr(self.config, attr))
            roots.append(root)
            if not self.excluded(root):
                if self.broad(root):
                    self.warning(f'{root} 是共享根目录，仅统计已知{self.rows[category]["label"]}', incomplete=True)
                else:
                    self.walk(root, category)
            for path in refs[category]:
                self.add(path, category)
            if category == 'knowledge':
                try:
                    for path in self.config.resolve_knowledge_paths().values():
                        item = self.path(path)
                        roots.append(item.parent)
                        self.add(item, category)
                except (OSError, ValueError, AttributeError) as exc:
                    self.warning(f'无法获取知识文件路径：{exc}', incomplete=True)
        self.current_category = 'cache'
        snapshots = self.path(self.config.work_dir) / 'snapshots'
        roots.append(snapshots)
        self.known_children(snapshots, ('refresh-',), 'cache')
        self.known_children(self.cache, ('qqdigest-backup-', 'qqdigest-check-', 'refresh-'), 'cache')
        self.current_category = 'backups'
        self.known_children(self.backups, ('QQDigest-',), 'backups', files=True)
        self.current_category = 'logs'
        logs = self.path(self.config.log_dir)
        roots.append(logs)
        if not self.excluded(logs):
            if self.broad(logs):
                self.warning(f'{logs} 是共享日志根目录，已跳过递归统计', incomplete=True)
            else:
                self.walk(logs, 'logs')
        self.current_category = None
        disk_rows = self.disks(roots)
        return {'categories': list(self.rows.values()), 'total_bytes': sum(row['size_bytes'] for row in self.rows.values()),
                'total_complete': all(row['complete'] for row in self.rows.values()),
                'disks': disk_rows, 'warnings': self.warnings,
                'paths': {'storage': str(self.storage), 'backups': str(self.backups), 'cache': str(self.cache)}}


def inventory(config, backup_root, temp_root):
    """Inspect existing files without creating schema, directories, or temp files."""
    return _Inventory(config, backup_root, temp_root).result()
