"""Validated recent rows from readable parts of an otherwise damaged NTQQ DB.

This is an optional, explicitly incomplete source. It never replaces nt_msg.db
or declares a damaged database healthy. All source connections are read-only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from .attachment_sources import attachment_refs
from .collector.protobuf import extract_message_text
from .forward_sources import forward_records


def build_message_media_snapshot(
    message_db: str | Path,
    fts_db: str | Path,
    output_path: str | Path,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> int:
    """Atomically write a clean recent media/sequence snapshot; return row count.

    Current FTS IDs, group IDs and timestamps establish exact anchors. A healthy
    peer/sequence index bounds subsequent reads away from damaged historical
    pages. A failed read or validation never publishes a partial new file.
    """
    end_ts = int(time.time()) if end_ts is None else end_ts
    start_ts = end_ts - 30 * 86400 if start_ts is None else start_ts
    if start_ts > end_ts:
        raise ValueError('invalid snapshot time window')
    output_path = Path(output_path)
    source_path, fts_path = Path(message_db), Path(fts_db)
    if output_path.resolve() in {source_path.resolve(), fts_path.resolve()}:
        raise ValueError('snapshot must not replace a source database')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix='.nt_msg_media-', suffix='.db', dir=output_path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    count = 0
    try:
        with closing(sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro', uri=True)) as source, \
                closing(sqlite3.connect(fts_path.resolve().as_uri() + '?mode=ro', uri=True)) as fts, \
                closing(sqlite3.connect(temporary)) as target:
            source.row_factory = sqlite3.Row
            anchors: dict[int, int] = {}
            rows = fts.execute('SELECT "40001","40021","40050" FROM group_msg_fts '
                'WHERE "40050">=? AND "40050"<=?', (start_ts, end_ts))
            for msg_id, peer, timestamp in rows:
                if not isinstance(timestamp, int):
                    continue
                if timestamp > end_ts or not str(peer).isdigit() or not isinstance(msg_id, int):
                    continue
                gid = int(peer)
                anchor = source.execute(
                    'SELECT "40003" FROM group_msg_table WHERE "40001"=? '
                    'AND "40021"=? AND "40027"=? AND "40050"=?',
                    (msg_id, str(gid), gid, timestamp),
                ).fetchone()
                if anchor and isinstance(anchor[0], int) and anchor[0] >= 0:
                    anchors[gid] = min(anchors.get(gid, anchor[0]), anchor[0])
            columns = {row[1] for row in source.execute('PRAGMA table_info(group_msg_table)')}
            sender_column = '"40033"' if '40033' in columns else 'NULL'
            target.execute('CREATE TABLE message_media ('
                'group_id INTEGER NOT NULL,msg_id TEXT NOT NULL,timestamp INTEGER NOT NULL,'
                'sender_uid TEXT NOT NULL,sender_qq INTEGER,message_sequence INTEGER NOT NULL,'
                'text TEXT NOT NULL,kind TEXT NOT NULL,content_json TEXT NOT NULL,'
                'PRIMARY KEY(group_id,msg_id,timestamp))')
            target.execute('CREATE INDEX message_media_window ON message_media(group_id,timestamp)')
            for gid, sequence in anchors.items():
                cursor = source.execute(
                    'SELECT "40001","40021","40050","40020","40003","40800",'
                    + sender_column + ' FROM group_msg_table '
                    'INDEXED BY group_msg_table_idx40027_40003 '
                    'WHERE "40027"=? AND "40003">=? ORDER BY "40003"',
                    (gid, max(sequence - 10, 0)),
                )
                for mid, peer, timestamp, uid, seq, blob, sender in cursor:
                    if (str(peer) != str(gid) or not isinstance(mid, int)
                            or not isinstance(timestamp, int) or not start_ts <= timestamp <= end_ts
                            or not isinstance(seq, int) or seq < 0):
                        continue
                    text, kind = extract_message_text(blob)
                    refs, forwards = attachment_refs(blob), forward_records(blob)
                    metadata = {'message_sequence': seq, 'attachment_source_unavailable': True}
                    if refs:
                        metadata['attachments'] = refs
                    if forwards:
                        metadata['forward_messages'] = forwards
                    target.execute('INSERT INTO message_media VALUES (?,?,?,?,?,?,?,?,?)',
                        (gid, str(mid), timestamp, str(uid or ''),
                         sender if isinstance(sender, int) and sender > 0 else None,
                         seq, text or ('[图片]' if any(r['kind'] == 'image' for r in refs) else ''),
                         kind, json.dumps(metadata, ensure_ascii=False)))
                    count += 1
            target.commit()
            if target.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('media snapshot integrity validation failed')
        os.replace(temporary, output_path)
        return count
    finally:
        temporary.unlink(missing_ok=True)
