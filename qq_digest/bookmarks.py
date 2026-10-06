"""Local bookmarks with validated sources and immutable text snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .catchup import report_item_key
from .message_context import message_context
from .report_sources import extract_report_sources, load_verified_report_sources
from .search import _like_pattern


class BookmarkConflict(ValueError):
    pass


def safe_resource_url(value):
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {'http', 'https'} and parsed.netloc and not any(
            ord(char) < 32 for char in value) else ''
    except ValueError:
        return ''


class BookmarkService:
    def __init__(self, archive, timezone_name='Asia/Shanghai'):
        self.archive = archive
        self.timezone_name = timezone_name

    def _group_name(self, group_id):
        row = self.archive.connection.execute('SELECT name FROM groups WHERE group_id=?', (group_id,)).fetchone()
        return row['name'] if row else str(group_id)

    def resolve(self, target):
        db = self.archive.connection
        kind = target['kind']
        if kind == 'message':
            row = db.execute('SELECT * FROM messages WHERE group_id=? AND msg_id=?',
                             (target['group_id'], target['msg_id'])).fetchone()
            if row is None:
                raise LookupError('原消息不可用')
            group_id = row['group_id']
            text = row['text']
            return dict(target=target, group_id=group_id, group_name=self._group_name(group_id),
                        title=text.strip().split('\n')[0][:80] or '原消息', text=text,
                        date=row['timestamp'], source_ids=[row['msg_id']], link='')
        if kind in {'resource', 'knowledge'}:
            row = db.execute('SELECT * FROM candidates WHERE candidate_id=?', (target['candidate_id'],)).fetchone()
            if row is None or (kind == 'resource' and row['candidate_type'] != 'resource'):
                raise LookupError('资源不可用')
            if kind == 'knowledge' and (row['status'] != 'confirmed' or not db.execute(
                'SELECT 1 FROM knowledge_items WHERE candidate_id=?', (target['candidate_id'],)).fetchone()):
                raise LookupError('知识条目尚未入库或已移除')
            return dict(target=target, group_id=row['group_id'], group_name=self._group_name(row['group_id']),
                        title=row['title'], text=row['content'] or row['excerpt'] or row['reason'],
                        date=row['created_date'], source_ids=json.loads(row['message_ids']),
                        link=safe_resource_url(row['link']))
        kind_report = target['report_kind']
        if kind_report == 'daily':
            row = db.execute('SELECT *,report_date AS start_date,report_date AS end_date FROM reports '
                             'WHERE report_id=?', (target['report_id'],)).fetchone()
        else:
            row = db.execute('SELECT * FROM manual_reports WHERE manual_report_id=?',
                             (target['report_id'],)).fetchone()
        if row is None:
            raise LookupError('摘要不可用')
        try:
            payload = json.loads(Path(row['json_path']).read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('摘要格式无效')
            if payload.get('evidence_version') == 1:
                items = load_verified_report_sources(db, payload, group_id=row['group_id'],
                    start_date=row['start_date'], end_date=row['end_date'], timezone_name=self.timezone_name)
            else:
                items = extract_report_sources({**payload, 'evidence_version': 1})
                for item in items or []:
                    item['source_ids'] = []
                    item['status'] = 'legacy'
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise LookupError('摘要内容不可用') from exc
        duplicates = {}
        for item in items or []:
            identity = json.dumps([item['section'], item['text'], item['source_ids']], ensure_ascii=False)
            occurrence = duplicates.get(identity, 0); duplicates[identity] = occurrence + 1
            key = report_item_key(kind_report, target['report_id'], item['section'], item['text'],
                                  item['source_ids'], occurrence)
            if key == target['point_key']:
                return dict(target=target, group_id=row['group_id'], group_name=self._group_name(row['group_id']),
                            title=item['text'][:80], text=item['text'], date=row['end_date'],
                            source_ids=item['source_ids'], section=item['section'], evidence_status=item['status'], link='')
        raise LookupError('摘要要点已变化，请刷新后收藏')

    @staticmethod
    def _present(row):
        result = dict(row)
        result.pop('source_key')
        result['snapshot'] = json.loads(result['snapshot'])
        return result

    def save(self, target):
        key = json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        existing = self.archive.connection.execute('SELECT * FROM bookmarks WHERE source_key=?', (key,)).fetchone()
        if existing:
            return {**self._present(existing), 'reused': True}
        snapshot = self.resolve(target)
        now = datetime.now(timezone.utc).isoformat()
        with self.archive.transaction():
            cursor = self.archive.connection.execute(
                'INSERT INTO bookmarks(source_key,kind,group_id,snapshot,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                (key, target['kind'], snapshot['group_id'], json.dumps(snapshot, ensure_ascii=False), now, now))
        return {**self.get(cursor.lastrowid), 'reused': False}

    def get(self, bookmark_id):
        row = self.archive.connection.execute('SELECT * FROM bookmarks WHERE bookmark_id=?', (bookmark_id,)).fetchone()
        if row is None:
            raise LookupError('收藏不存在或已取消')
        return self._present(row)

    def detail(self, bookmark_id):
        item = self.get(bookmark_id)
        target = item['snapshot']['target']
        try:
            current = self.resolve(target)
            available = True
        except LookupError:
            current = None; available = False
        url = ''
        if available:
            if target['kind'] == 'knowledge':
                url = f"/knowledge?item_id={target['candidate_id']}"
            elif target['kind'] == 'resource':
                url = current['link']
            elif target['kind'] == 'report':
                url = f"/reports?kind={target['report_kind']}&id={target['report_id']}"
        return {**item, 'origin_available': available, 'origin_url': url, 'origin': current}

    def list_items(self, *, status='pending', kind=None, q='', page=1):
        clauses, params = [], []
        if status != 'all': clauses.append('status=?'); params.append(status)
        if kind: clauses.append('kind=?'); params.append(kind)
        if q.strip():
            clauses.append("(json_extract(snapshot,'$.title') LIKE ? ESCAPE '\\' OR "
                           "json_extract(snapshot,'$.text') LIKE ? ESCAPE '\\' OR "
                           "json_extract(snapshot,'$.link') LIKE ? ESCAPE '\\')")
            params.extend([_like_pattern(q.strip())] * 3)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        db = self.archive.connection
        total = db.execute('SELECT COUNT(*) FROM bookmarks' + where, params).fetchone()[0]
        rows = db.execute('SELECT * FROM bookmarks' + where + ' ORDER BY bookmark_id DESC LIMIT 20 OFFSET ?',
                          [*params, (page - 1) * 20]).fetchall()
        items = [self._present(row) for row in rows]
        for item in items:
            item['snapshot']['text'] = item['snapshot']['text'][:240]
        counts = dict(db.execute('SELECT status,COUNT(*) FROM bookmarks GROUP BY status').fetchall())
        return dict(items=items, total=total, page=page, page_size=20, counts=counts)

    def update(self, bookmark_id, status, expected_revision):
        with self.archive.transaction():
            self.get(bookmark_id)
            result = self.archive.connection.execute(
                'UPDATE bookmarks SET status=?,revision=revision+1,updated_at=? WHERE bookmark_id=? AND revision=?',
                (status, datetime.now(timezone.utc).isoformat(), bookmark_id, expected_revision))
            if not result.rowcount: raise BookmarkConflict('收藏已更新，请刷新后重试')
        return self.get(bookmark_id)

    def remove(self, bookmark_id, expected_revision):
        with self.archive.transaction():
            self.get(bookmark_id)
            result = self.archive.connection.execute('DELETE FROM bookmarks WHERE bookmark_id=? AND revision=?',
                                                     (bookmark_id, expected_revision))
            if not result.rowcount: raise BookmarkConflict('收藏已更新，请刷新后重试')
        return {'removed': True}

    def context(self, bookmark_id, direction='around', cursor=None):
        target = self.get(bookmark_id)['snapshot']['target']
        if target['kind'] != 'message': raise LookupError('这条收藏不是原消息')
        return message_context(self.archive, target['group_id'], target['msg_id'], direction=direction, cursor=cursor)
