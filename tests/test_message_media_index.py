import sqlite3
from datetime import datetime, timezone

import pytest

from qq_digest.collector.ntqq import NTQQCollector
from qq_digest.collector.protobuf import make_bytes_field, make_varint_field
from tests.test_ntqq_collector import ntqq_dbs, ntqq_fts_dbs


TS = 1759851722


def current_database(path, *, damaged=True):
    image = make_bytes_field(40800, make_varint_field(45002, 2)
        + make_bytes_field(45402, b'photo.png') + make_bytes_field(45406, b'x' * 16))
    text = make_bytes_field(40800, make_varint_field(45002, 1)
        + make_bytes_field(45101, b'body'))
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE group_msg_table ("40001" INTEGER PRIMARY KEY,'
            '"40020" TEXT,"40021" TEXT,"40027" INTEGER,"40033" INTEGER,'
            '"40050" INTEGER,"40003" INTEGER,"40800" BLOB)')
        db.execute('CREATE INDEX group_msg_table_idx40027_40003 ON group_msg_table("40027","40003")')
        db.executemany('INSERT INTO group_msg_table VALUES (?,?,?,?,?,?,?,?)', [
            (8101, 'u_sender', '100', 100, 12345, TS, 100, image),
            (9902, 'u_sender', '100', 100, 12345, TS + 10, 101, image),
            (8102, '12345', '100', 100, 12345, TS + 1, 102, text),
            (9991, 'u_sender', '200', 100, 12345, TS + 12, 103, image),
            (9992, 'u_sender', '100', 100, 12345, TS - 1, 104, image),
        ])
        db.execute('CREATE TABLE broken_history (value BLOB)')
        db.execute('INSERT INTO broken_history VALUES (?)', (b'history',))
        page = db.execute("SELECT rootpage FROM sqlite_master WHERE name='broken_history'").fetchone()[0]
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
    if damaged:
        with path.open('r+b') as handle:
            handle.seek((page - 1) * page_size)
            handle.write(b'\xff')
    return path


def build(source, fts, destination, **kwargs):
    from qq_digest.message_media_index import build_message_media_snapshot
    return build_message_media_snapshot(source, fts, destination,
        start_ts=TS, end_ts=TS + 100, **kwargs)


def collect(path):
    return {m.msg_id: m for m in NTQQCollector(path).collect(100,
        datetime.fromtimestamp(TS, timezone.utc), datetime.fromtimestamp(TS + 100, timezone.utc))}


def test_clean_partial_snapshot_recovers_current_media_and_sequences(ntqq_fts_dbs, tmp_path):
    source = current_database(tmp_path / 'current.db')
    before = source.read_bytes()
    destination = ntqq_fts_dbs / 'nt_msg_media.db'
    assert build(source, ntqq_fts_dbs / 'group_msg_fts.db', destination) == 3
    with sqlite3.connect(destination) as db:
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]
    messages = collect(ntqq_fts_dbs)
    assert messages['8101'].text == '普通正文'
    assert messages['8101'].content_json['attachments'][0]['md5'] == '78' * 16
    assert messages['9902'].text == '[图片]'
    assert messages['9902'].content_json['message_sequence'] == 101
    assert messages['8102'].content_json['message_sequence'] == 102
    assert messages['8102'].content_json['attachment_source_unavailable'] is True
    assert '9991' not in messages and '9992' not in messages
    assert source.read_bytes() == before


def test_snapshot_merge_guards_timestamp_and_keeps_authoritative_refs(ntqq_fts_dbs, tmp_path):
    source = current_database(tmp_path / 'current.db')
    build(source, ntqq_fts_dbs / 'group_msg_fts.db', ntqq_fts_dbs / 'nt_msg_media.db')
    with sqlite3.connect(ntqq_fts_dbs / 'nt_msg.db') as db:
        image = make_bytes_field(40800, make_varint_field(45002, 2)
            + make_bytes_field(45302, b'y' * 16))
        db.execute('INSERT INTO group_msg_table VALUES (?,?,?,?,?,?)',
            (8101, 'u_sender', '100', 100, TS, image))
    with sqlite3.connect(ntqq_fts_dbs / 'nt_msg_media.db') as db:
        db.execute('UPDATE message_media SET timestamp=timestamp+1 WHERE msg_id=?', ('8102',))
    messages = collect(ntqq_fts_dbs)
    assert messages['8101'].content_json['attachments'][0]['md5'] == '79' * 16
    assert 'message_sequence' not in messages['8102'].content_json


def test_anchor_requires_exact_group_and_timestamp_and_build_failure_is_atomic(ntqq_fts_dbs, tmp_path):
    source = current_database(tmp_path / 'current.db')
    destination = tmp_path / 'snapshot.db'
    build(source, ntqq_fts_dbs / 'group_msg_fts.db', destination)
    before = destination.read_bytes()
    with sqlite3.connect(source) as db:
        db.execute('DROP INDEX group_msg_table_idx40027_40003')
    with pytest.raises(sqlite3.DatabaseError):
        build(source, ntqq_fts_dbs / 'group_msg_fts.db', destination)
    assert destination.read_bytes() == before
    with sqlite3.connect(source) as db:
        db.execute('CREATE INDEX group_msg_table_idx40027_40003 ON group_msg_table("40027","40003")')
        db.execute('UPDATE group_msg_table SET "40050"="40050"+1 WHERE "40001"=8101')
        db.execute('UPDATE group_msg_table SET "40021"=? WHERE "40001"=8102', ('200',))
    assert build(source, ntqq_fts_dbs / 'group_msg_fts.db', destination) == 0


def test_unreadable_snapshot_keeps_searchable_text_with_incomplete_flag(ntqq_fts_dbs):
    (ntqq_fts_dbs / 'nt_msg_media.db').write_bytes(b'broken')
    messages = collect(ntqq_fts_dbs)
    assert messages['8101'].text == '普通正文'
    assert messages['8101'].content_json['attachment_source_unavailable'] is True


def test_fts_anchor_scan_does_not_assume_rowids_are_chronological(ntqq_fts_dbs, tmp_path):
    source = current_database(tmp_path / 'current.db')
    with sqlite3.connect(ntqq_fts_dbs / 'group_msg_fts.db') as db:
        db.execute('INSERT INTO group_msg_fts("41700","40001","40021","40050") VALUES (?,?,?,?)',
            (1000, 1, '100', TS - 100))
    assert build(source, ntqq_fts_dbs / 'group_msg_fts.db', tmp_path / 'snapshot.db') == 3


def test_stale_verified_main_marks_unmatched_current_fts_as_incomplete(ntqq_fts_dbs):
    messages = collect(ntqq_fts_dbs)
    assert messages['8101'].content_json['attachment_source_unavailable'] is True


def test_clean_modern_main_preserves_text_sequence_without_optional_snapshot(ntqq_fts_dbs, tmp_path):
    source = current_database(tmp_path / 'current.db', damaged=False)
    (ntqq_fts_dbs / 'nt_msg.db').write_bytes(source.read_bytes())
    with sqlite3.connect(ntqq_fts_dbs / 'nt_msg.db') as db:
        # Highest rowid is old: its position must not truncate current metadata.
        db.execute('INSERT INTO group_msg_table VALUES (?,?,?,?,?,?,?,?)',
            (99999, '12345', '100', 100, 12345, TS - 100, 90, b''))
    messages = collect(ntqq_fts_dbs)
    assert messages['8102'].text == '@成员 回复内容'
    assert messages['8102'].content_json['message_sequence'] == 102
    assert 'attachment_source_unavailable' not in messages['8102'].content_json


@pytest.mark.parametrize('snapshot_readable', [True, False])
def test_old_optional_snapshot_does_not_mark_verified_current_main_incomplete(
        ntqq_fts_dbs, tmp_path, snapshot_readable):
    source = current_database(tmp_path / 'current.db', damaged=False)
    snapshot = ntqq_fts_dbs / 'nt_msg_media.db'
    build(source, ntqq_fts_dbs / 'group_msg_fts.db', snapshot)
    if not snapshot_readable:
        snapshot.write_bytes(b'unreadable stale optional')
    (ntqq_fts_dbs / 'nt_msg.db').write_bytes(source.read_bytes())
    messages = collect(ntqq_fts_dbs)
    assert messages['8102'].content_json['message_sequence'] == 102
    assert 'attachment_source_unavailable' not in messages['8102'].content_json


@pytest.mark.parametrize('fts_uid,expected', [
    ('u_sender', 12345), ('u_other', None), ('45678', 45678)])
def test_recovered_exact_duplicate_fills_only_missing_nonconflicting_sender(
        ntqq_fts_dbs, tmp_path, fts_uid, expected):
    source = current_database(tmp_path / 'current.db')
    build(source, ntqq_fts_dbs / 'group_msg_fts.db', ntqq_fts_dbs / 'nt_msg_media.db')
    with sqlite3.connect(ntqq_fts_dbs / 'group_msg_fts.db') as db:
        db.execute('UPDATE group_msg_fts SET "40020"=? WHERE "40001"=8101', (fts_uid,))
    assert collect(ntqq_fts_dbs)['8101'].sender_qq == expected
