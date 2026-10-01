"""Immutable report snapshots and human corrections stored with the archive."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class RevisionConflict(ValueError):
    pass


class ReportRevisionService:
    def __init__(self, archive):
        self.archive = archive
        self.db = archive.connection

    def report(self, kind, report_id):
        tables = {'daily': ('reports', 'report_id'), 'range': ('manual_reports', 'manual_report_id')}
        if kind not in tables:
            raise LookupError('报告类型不存在')
        table, key = tables[kind]
        row = self.db.execute(f'SELECT * FROM {table} WHERE {key}=?', (report_id,)).fetchone()
        if row is None:
            raise LookupError('报告不存在')
        return row

    def current_version(self, kind, report_id):
        self.report(kind, report_id)
        return self.db.execute('SELECT COALESCE(MAX(version),0) FROM report_revisions '
                               'WHERE report_kind=? AND report_id=?', (kind, report_id)).fetchone()[0]

    def check_version(self, kind, report_id, expected_version):
        if self.current_version(kind, report_id) != expected_version:
            raise RevisionConflict('报告已有新版本，请刷新详情后再操作')

    def _legacy(self, kind, report_id, row):
        try:
            markdown = Path(row['markdown_path']).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            markdown = None
        try:
            payload = json.loads(Path(row['json_path']).read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                payload = None
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = None
        return dict(report_kind=kind, report_id=report_id, version=0, markdown=markdown,
                    payload=payload, origin='legacy', reason='启用版本记录前的报告；更早历史无法恢复',
                    created_at=row['updated_at'] if kind == 'range' else row['created_at'],
                    content_available=markdown is not None and payload is not None)

    def _append(self, kind, report_id, row, markdown, payload, origin, reason, created_at=None):
        version = self.current_version(kind, report_id) + 1
        self.db.execute('''INSERT INTO report_revisions
            (report_kind,report_id,group_id,version,markdown,payload,origin,reason,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''', (kind, report_id, row['group_id'], version, markdown,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                origin, reason, created_at or datetime.now(timezone.utc).isoformat()))
        return version

    def capture_legacy_in_transaction(self, kind, report_id):
        if self.current_version(kind, report_id):
            return
        row = self.report(kind, report_id)
        legacy = self._legacy(kind, report_id, row)
        self._append(kind, report_id, row, legacy['markdown'], legacy['payload'],
                     'legacy', legacy['reason'], legacy['created_at'])

    def record_generated_in_transaction(self, kind, report_id, markdown, payload, reason):
        if not isinstance(markdown, str) or not isinstance(payload, dict):
            raise ValueError('版本内容无效')
        return self._append(kind, report_id, self.report(kind, report_id), markdown,
                            payload, 'generated', reason.strip() or 'AI 生成')

    def versions(self, kind, report_id, *, page=1, page_size=10):
        current = self.current_version(kind, report_id)
        total = current or 1
        page = min(max(1, page), max(1, (total + page_size - 1)//page_size))
        if current:
            rows = self.db.execute('''SELECT version,origin,reason,created_at,
                (markdown IS NOT NULL AND payload IS NOT NULL) AS content_available
                FROM report_revisions WHERE report_kind=? AND report_id=?
                ORDER BY version DESC LIMIT ? OFFSET ?''', (kind, report_id, page_size, (page-1)*page_size))
            versions = [dict(row) for row in rows]
        else:
            item = self._legacy(kind, report_id, self.report(kind, report_id))
            versions = [{key:item[key] for key in ('version','origin','reason','created_at','content_available')}]
        return dict(current_version=current, versions=versions, total=total, page=page, page_size=page_size)

    def version(self, kind, report_id, version):
        current = self.current_version(kind, report_id)
        if not current and version == 0:
            return self._legacy(kind, report_id, self.report(kind, report_id))
        row = self.db.execute('SELECT * FROM report_revisions WHERE report_kind=? AND report_id=? AND version=?',
                              (kind, report_id, version)).fetchone()
        if row is None:
            raise LookupError('历史版本不存在')
        item = dict(row)
        item['payload'] = json.loads(item['payload']) if item['payload'] else None
        item['content_available'] = item['markdown'] is not None and item['payload'] is not None
        return item

    def corrections(self, kind, report_id):
        self.report(kind, report_id)
        return [dict(row) for row in self.db.execute('''SELECT c.*,r.version FROM report_corrections c
            JOIN report_revisions r ON r.revision_id=c.revision_id
            WHERE r.report_kind=? AND r.report_id=? ORDER BY c.correction_id DESC''', (kind, report_id))]

    def add_correction(self, kind, report_id, *, expected_version, category, excerpt, correction, reason):
        if category not in {'missing','error','noise'}:
            raise ValueError('纠错类型无效')
        if any(not isinstance(value, str) or not value.strip() or len(value)>4000 for value in (correction, reason)):
            raise ValueError('修正意见和理由须填写，且不能超过 4000 字')
        if not isinstance(excerpt, str) or len(excerpt)>4000:
            raise ValueError('原段落不能超过 4000 字')
        with self.archive.transaction():
            self.check_version(kind, report_id, expected_version)
            self.capture_legacy_in_transaction(kind, report_id)
            version = self.current_version(kind, report_id)
            revision_id = self.db.execute('SELECT revision_id FROM report_revisions '
                'WHERE report_kind=? AND report_id=? AND version=?', (kind,report_id,version)).fetchone()[0]
            cursor = self.db.execute('''INSERT INTO report_corrections
                (revision_id,category,excerpt,correction,reason,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'open',?,?)''', (revision_id,category,excerpt.strip(),correction.strip(),reason.strip(),
                    datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat()))
            correction_id = cursor.lastrowid
        return next(note for note in self.corrections(kind,report_id) if note['correction_id']==correction_id)

    def set_correction_status(self, kind, report_id, correction_id, status):
        if status not in {'open','resolved'}:
            raise ValueError('处理状态无效')
        notes = self.corrections(kind,report_id)
        if not any(note['correction_id']==correction_id for note in notes):
            raise LookupError('纠错记录不存在')
        with self.archive.transaction():
            self.db.execute('UPDATE report_corrections SET status=?,updated_at=? WHERE correction_id=?',
                            (status,datetime.now(timezone.utc).isoformat(),correction_id))
        return {'correction_id':correction_id, 'status':status}
