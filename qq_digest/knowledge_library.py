"""Browse persisted knowledge and explicitly save an archived source message."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from .candidate_context import candidate_source_context
from .candidates import CandidateService
from .knowledge import KnowledgeItem, remove_item
from .group_selection import normalize_group_selection
from .search import _like_pattern


class KnowledgeLibrary:
    def __init__(self, archive, writer, timezone_name='Asia/Shanghai', group_names=None):
        self.archive, self.writer = archive, writer
        self.group_names=group_names or {}
        self.candidates = CandidateService(archive)
        self.zone = ZoneInfo(timezone_name)

    def _select(self):
        return (" FROM knowledge_items k JOIN candidates c ON c.candidate_id=k.candidate_id "
                "LEFT JOIN groups g ON g.group_id=c.group_id ")

    @staticmethod
    def _item(row):
        return {'item_id': row['candidate_id'], 'title': row['title'],
                'item_type': row['candidate_type'], 'group_id': row['group_id'],
                'group_name': row['group_name'] or str(row['group_id']),
                'date': row['created_date'], 'saved_at': row['written_at'],
                'content': row['content'], 'reason': row['reason'], 'link': row['link'],
                'excerpt': row['excerpt'], 'message_ids': json.loads(row['message_ids'])}

    def list_items(self, *, q='', group_id=None, item_type=None, page=1, group_ids=None):
        selected = normalize_group_selection(group_id, group_ids)
        clauses, params = ["c.status='confirmed'"], []
        if selected:
            clauses.append('c.group_id IN (' + ','.join('?' for _ in selected) + ')')
            params.extend(selected)
        if item_type:
            clauses.append('c.candidate_type=?'); params.append(item_type)
        if q.strip():
            clauses.append("(c.title LIKE ? ESCAPE '\\' OR c.content LIKE ? ESCAPE '\\' "
                           "OR c.reason LIKE ? ESCAPE '\\' OR c.link LIKE ? ESCAPE '\\' "
                           "OR c.excerpt LIKE ? ESCAPE '\\')")
            params.extend([_like_pattern(q.strip())] * 5)
        where = ' WHERE ' + ' AND '.join(clauses)
        db = self.archive.connection
        total = db.execute('SELECT COUNT(*)' + self._select() + where, params).fetchone()[0]
        rows = db.execute('SELECT c.*,g.name AS group_name,k.written_at' + self._select() + where
                          + ' ORDER BY k.written_at DESC,c.candidate_id DESC LIMIT 20 OFFSET ?',
                          [*params, (page-1)*20]).fetchall()
        items = [self._item(row) for row in rows]
        for item in items:
            body = item.pop('content') or item['excerpt'] or item['reason']
            item['preview'] = body[:240]
            item.pop('message_ids')
        return {'items':items, 'total':total, 'page':page, 'page_size':20}

    def detail(self, candidate_id):
        row = self.archive.connection.execute(
            'SELECT c.*,g.name AS group_name,k.written_at' + self._select()
            + " WHERE c.candidate_id=? AND c.status='confirmed'", (candidate_id,)).fetchone()
        if row is None:
            raise LookupError('知识条目不存在或尚未入库')
        return {**self._item(row), **candidate_source_context(self.archive, self.candidates.get(candidate_id))}

    def source(self, group_id, msg_id):
        row = self.archive.connection.execute(
            'SELECT m.*,g.name AS group_name FROM messages m LEFT JOIN groups g '
            'ON g.group_id=m.group_id WHERE m.group_id=? AND m.msg_id=?', (group_id,msg_id)).fetchone()
        if row is None:
            raise LookupError('原消息不存在于所选群的本地归档')
        if not row['text'].strip():
            raise ValueError('这条消息没有可保存的文字内容')
        return {'group_id':group_id,'msg_id':msg_id,'group_name':row['group_name'] or str(group_id),
                'date':datetime.fromisoformat(row['timestamp']).astimezone(self.zone).date().isoformat(),
                'timestamp':row['timestamp'],'text':row['text']}

    @contextmanager
    def _publication(self):
        # Take the SQLite writer lock before reading file snapshots. Roll back
        # files while it is still held so another publisher cannot be erased.
        db=self.archive.connection
        db.execute('BEGIN IMMEDIATE')
        originals={}
        def remember(path):
            if path not in originals:
                originals[path]=path.read_bytes() if path.exists() else None
        try:
            yield remember
            db.commit()
        except Exception:
            try:
                for path,original in originals.items():self._restore(path,original)
            finally:
                db.rollback()
            raise

    def save_message(self, *, group_id, msg_id, title, item_type, reason='', link=''):
        with self._publication() as remember:
            source=self.source(group_id,msg_id)
            rows=self.archive.connection.execute(
                "SELECT c.candidate_id,c.message_ids" + self._select()
                + " WHERE c.status='confirmed' AND c.group_id=? AND c.candidate_type=?",
                (group_id,item_type)).fetchall()
            for row in rows:
                if json.loads(row['message_ids']) == [msg_id]:
                    return {'item_id':row['candidate_id'],'reused':True}
            candidate_id=self.candidates.create_in_transaction(
                group_id=group_id,created_date=source['date'],candidate_type=item_type,
                title=title,link=link,reason=reason,content=source['text'],
                excerpt=source['text'][:200],message_ids=[msg_id],human_edited=True)
            candidate=self.candidates.get(candidate_id)
            remember(self.writer.path_for(candidate.candidate_type))
            self.archive.connection.execute(
                "UPDATE candidates SET ignore_reason='' WHERE candidate_id=?", (candidate_id,))
            self._publish(candidate_id)
            return {'item_id':candidate_id,'reused':False}

    @staticmethod
    def _restore(path, original):
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)

    def _publish(self, candidate_id):
        candidate=self.candidates.get(candidate_id)
        group=self.archive.connection.execute('SELECT name FROM groups WHERE group_id=?',
                                               (candidate.group_id,)).fetchone()
        self.writer.write(KnowledgeItem(
            item_id=str(candidate_id),date=candidate.created_date,
            category='资源' if candidate.candidate_type=='resource' else '经验',
            source_group=group['name'] if group else self.group_names.get(candidate.group_id,str(candidate.group_id)),title=candidate.title,
            link=candidate.link,value=candidate.reason,excerpt=candidate.excerpt,content=candidate.content),
            candidate.candidate_type)
        now=datetime.now(timezone.utc).isoformat()
        self.archive.connection.execute(
            'INSERT OR IGNORE INTO knowledge_items(item_id,candidate_id,markdown_path,written_at) VALUES(?,?,?,?)',
            (str(candidate_id),candidate_id,str(self.writer.path_for(candidate.candidate_type)),now))
        self.archive.connection.execute("UPDATE candidates SET status='confirmed',updated_at=?,revision=revision+1,human_edited=1 WHERE candidate_id=?",
                                        (now,candidate_id))

    def confirm_candidate(self, candidate_id):
        with self._publication() as remember:
            candidate=self.candidates.get(candidate_id)
            if candidate.status=='confirmed':return
            if candidate.status not in {'pending','later'}:
                raise ValueError('只有待审核或稍后的候选可以入库')
            remember(self.writer.path_for(candidate.candidate_type))
            self._publish(candidate_id)

    def undo_candidate(self, candidate_id):
        with self._publication() as remember:
            candidate=self.candidates.get(candidate_id)
            if candidate.status!='confirmed':raise ValueError('只有已入库的候选可以撤销')
            row=self.archive.connection.execute('SELECT markdown_path FROM knowledge_items WHERE candidate_id=?',(candidate_id,)).fetchone()
            if row is None:raise ValueError('找不到对应的知识库条目')
            from pathlib import Path
            path=Path(row['markdown_path']);remember(path)
            remove_item(path,str(candidate_id))
            self.archive.connection.execute('DELETE FROM knowledge_items WHERE candidate_id=?',(candidate_id,))
            self.archive.connection.execute("UPDATE candidates SET status='pending',updated_at=?,revision=revision+1 WHERE candidate_id=?",(datetime.now(timezone.utc).isoformat(),candidate_id))
        return {'status':'pending'}
