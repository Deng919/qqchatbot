"""Versioned retention settings and cleanup history in the current archive."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3


class StorageConflict(ValueError):
    """Another window has changed the retention policy."""


SCHEMA = '''
CREATE TABLE IF NOT EXISTS storage_retention_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    backup_days INTEGER NOT NULL DEFAULT 30,
    backup_keep INTEGER NOT NULL DEFAULT 3,
    cache_days INTEGER NOT NULL DEFAULT 7,
    revision INTEGER NOT NULL DEFAULT 0,
    last_successful_automatic_at TEXT
);
CREATE TABLE IF NOT EXISTS storage_cleanup_records (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    automatic INTEGER NOT NULL CHECK(automatic IN (0,1)),
    result_json TEXT NOT NULL
);
'''
FIELDS = {'enabled', 'backup_days', 'backup_keep', 'cache_days'}


def _safe_json(value, depth=0):
    """Bound user-facing diagnostics without serializing arbitrary objects or secrets."""
    if depth > 6:
        return '[已截断]'
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        return {key[:100]: _safe_json(item, depth + 1)
                for key, item in list(value.items())[:100]
                if isinstance(key, str) and not any(secret in key.casefold()
                    for secret in ('password', 'secret', 'token', 'api_key', 'authorization'))}
    return None


class StoragePolicy:
    def __init__(self, archive_path, now=None):
        self.path = Path(archive_path)
        self.now = now or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.executescript(SCHEMA)
            db.execute('INSERT OR IGNORE INTO storage_retention_settings(singleton) VALUES(1)')
            db.commit()
            yield db
        finally:
            db.close()

    def _time(self, now=None):
        value = self.now() if now is None else now
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('清理时钟必须包含时区')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _settings(db):
        row = dict(db.execute('SELECT enabled,backup_days,backup_keep,cache_days,revision '
                              'FROM storage_retention_settings WHERE singleton=1').fetchone())
        row['enabled'] = bool(row['enabled'])
        return row

    def settings(self):
        with self._db() as db:
            return self._settings(db)

    def update(self, values, expected_revision):
        if not isinstance(values, dict) or set(values) != FIELDS:
            raise ValueError('清理设置字段无效')
        if type(values['enabled']) is not bool:
            raise ValueError('自动清理开关必须为布尔值')
        for key in ('backup_days', 'cache_days', 'backup_keep'):
            maximum = 100 if key == 'backup_keep' else 3650
            if type(values[key]) is not int or not 1 <= values[key] <= maximum:
                raise ValueError(f'{key} 必须为 1–{maximum} 的整数')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('设置版本无效')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE storage_retention_settings SET enabled=?,backup_days=?,backup_keep=?,'
                                 'cache_days=?,revision=revision+1 WHERE singleton=1 AND revision=?',
                                 (int(values['enabled']), values['backup_days'], values['backup_keep'],
                                  values['cache_days'], expected_revision))
            if changed.rowcount != 1:
                raise StorageConflict('清理设置已在其他窗口更新，请刷新后再保存')
            result = self._settings(db)
            db.commit()
            return result

    def record(self, result, automatic=False):
        if not isinstance(result, dict) or type(automatic) is not bool:
            raise ValueError('清理结果格式无效')
        clean = _safe_json(result)
        encoded = json.dumps(clean, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 256 * 1024:
            clean = {key: clean[key] for key in ('status', 'deleted_count', 'deleted_bytes') if key in clean}
            clean['truncated'] = True
            encoded = json.dumps(clean, ensure_ascii=False)
        timestamp = self._time().isoformat()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO storage_cleanup_records(recorded_at,automatic,result_json) VALUES(?,?,?)',
                       (timestamp, int(automatic), encoded))
            if automatic and result.get('status') == 'success':
                db.execute('UPDATE storage_retention_settings SET last_successful_automatic_at=? WHERE singleton=1',
                           (timestamp,))
            db.execute('DELETE FROM storage_cleanup_records WHERE record_id NOT IN '
                       '(SELECT record_id FROM storage_cleanup_records ORDER BY record_id DESC LIMIT 100)')
            db.commit()
        return dict(clean, recorded_at=timestamp, automatic=automatic)

    def last_result(self):
        with self._db() as db:
            row = db.execute('SELECT recorded_at,automatic,result_json FROM storage_cleanup_records '
                             'ORDER BY record_id DESC LIMIT 1').fetchone()
            if row is None:
                return None
            return dict(json.loads(row['result_json']), recorded_at=row['recorded_at'], automatic=bool(row['automatic']))

    def automatic_due(self, now=None):
        moment = self._time(now)
        with self._db() as db:
            row = db.execute('SELECT enabled,last_successful_automatic_at FROM storage_retention_settings '
                             'WHERE singleton=1').fetchone()
        if not row['enabled']:
            return False
        if not row['last_successful_automatic_at']:
            return True
        last = datetime.fromisoformat(row['last_successful_automatic_at'])
        return moment - last >= timedelta(hours=24)
