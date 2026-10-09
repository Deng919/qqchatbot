"""Frozen-scope batch collection, with one refresh and independent group commits."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime

from .archive import Archive
from .collector.ntqq import NTQQCollector
from .group_selection import normalize_group_selection


def require_existing_groups(archive, group_ids):
    ids = normalize_group_selection(group_ids=group_ids, required=True)
    known = {row['group_id'] for row in archive.connection.execute('SELECT group_id FROM groups')}
    missing = [gid for gid in ids if gid not in known]
    if missing:
        raise KeyError('群组不存在：' + ', '.join(map(str, missing)))
    return ids


@contextmanager
def atomic_group_import(archive):
    """Let existing archive methods join this worker's one group transaction.

    Archive methods own their transactions and would otherwise commit the ingest
    before writing its success state. The worker owns this private connection;
    nested method contexts defer both commit and rollback to the outer context.
    """
    original = archive.transaction
    @contextmanager
    def joined():
        yield
    archive.transaction = joined
    try:
        with original():
            yield
    finally:
        archive.transaction = original


def batch_totals(results):
    return {'groups': len(results), 'success': sum(r['status']=='success' for r in results),
            'failed': sum(r['status']=='failed' for r in results),
            **{key: sum(r.get(key,0) for r in results) for key in ('total','inserted','skipped','missing_count')}}


def collect_batch(*, config, group_ids, start: datetime, end: datetime,
                  refresh: bool = True, dry_run: bool = False, collector=None, prepare=None):
    archive = Archive.open(config.archive_path)
    try:
        ids = require_existing_groups(archive, group_ids)
        results = []
        if refresh:
            try:
                if prepare:
                    prepare()
                else:
                    from .refresh import refresh_database
                    refreshed = refresh_database(qq_number=config.ntqq.qq_number, output_dir=config.ntqq.db_dir,
                        snapshot_root=config.work_dir/'snapshots', cache_owner=config.data_dir)
                    if not refreshed.success:
                        raise RuntimeError(f'数据库刷新失败：{refreshed.message}')
            except Exception as exc:
                error = ' '.join(str(exc).split())[:1000] or type(exc).__name__
                for gid in ids:
                    if not dry_run:
                        archive.mark_sync_failure(group_id=gid, status='manual_collect_failed', error=error)
                    results.append({'group_id':gid, 'status':'failed', 'error':error,
                                    'total':0, 'inserted':0, 'skipped':0, 'not_executed':True})
                return {'results':results, 'totals':batch_totals(results)}
        active = collector or NTQQCollector(db_dir=config.ntqq.db_dir,
            qq_number=config.ntqq.qq_number, timezone_name=config.ntqq.timezone)
        attachment_messages = []
        for gid in ids:
            try:
                source = list(active.collect(gid, start, end))
                unique = {}
                for message in source:
                    if message.group_id != gid:
                        raise ValueError('源库返回了其他群的消息')
                    if start <= message.timestamp <= end:
                        unique.setdefault(message.msg_id, message)
                messages = list(unique.values())
                present = set()
                keys = list(unique)
                for offset in range(0,len(keys),500):
                    batch = keys[offset:offset+500]
                    present.update(row['msg_id'] for row in archive.connection.execute(
                        f"SELECT msg_id FROM messages WHERE group_id=? AND msg_id IN ({','.join('?' for _ in batch)})",
                        [gid,*batch]))
                counts = Counter(m.timestamp.astimezone(start.tzinfo).date().isoformat()
                    for m in messages if m.msg_id not in present)
                inserted = 0
                if not dry_run:
                    with atomic_group_import(archive):
                        result = archive.ingest(messages)
                        archive.mark_manual_collect_success(group_id=gid)
                        inserted = result.inserted
                    attachment_messages.extend(messages)
                results.append({'group_id':gid, 'status':'success', 'total':len(source),
                    'inserted':inserted, 'skipped':len(source)-sum(counts.values()),
                    'missing_count':sum(counts.values()),
                    'missing_days':[{'date':day,'count':counts[day]} for day in sorted(counts)]})
            except Exception as exc:
                error = ' '.join(str(exc).split())[:1000] or type(exc).__name__
                if not dry_run:
                    archive.mark_sync_failure(group_id=gid, status='manual_collect_failed', error=error)
                results.append({'group_id':gid,'status':'failed','error':error,'total':0,'inserted':0,'skipped':0})
        if not dry_run and attachment_messages:
            from .attachments import AttachmentService
            try:
                # Preserve automatic reading without multiplying its 20-item
                # limit by the number of selected groups.
                AttachmentService(archive,config).prepare(attachment_messages)
            except Exception as exc:
                warning = ' '.join(str(exc).split())[:300] or type(exc).__name__
                for result in results:
                    if result['status']=='success': result['attachment_warning'] = warning
        return {'results':results, 'totals':batch_totals(results)}
    finally:
        archive.close()
