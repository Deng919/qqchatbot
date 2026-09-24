"""Tests for the NTQQCollector.

Uses temporary SQLite databases with the NTQQ schema to avoid touching real
data.  Tests cover group discovery, message collection, time filtering, and
corruption recovery.
"""

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from qq_digest.collector.base import CollectionIncompleteError
from qq_digest.collector.ntqq import NTQQCollector
from qq_digest.collector.protobuf import make_bytes_field, make_varint_field


@pytest.fixture
def ntqq_dbs(tmp_path):
    """Create minimal NTQQ databases in tmp_path."""
    db_dir = tmp_path / "ntqq"
    db_dir.mkdir()

    # group_info.db with group_list table
    gidb_path = db_dir / "group_info.db"
    conn = sqlite3.connect(gidb_path)
    conn.execute(
        'CREATE TABLE group_list ("60001" INTEGER, "60007" TEXT, "60040" BLOB)'
    )
    # Group 100: peer_uid u_testgroup100
    conn.execute(
        'INSERT INTO group_list VALUES (100, "测试群100", ?)',
        (b"\xc2\xa8\x1d\x20u_testgroup100\x90\xbe\x20\x00",),
    )
    # Group 200: peer_uid u_testgroup200
    conn.execute(
        'INSERT INTO group_list VALUES (200, "测试群200", ?)',
        (b"\xc2\xa8\x1d\x20u_testgroup200\x90\xbe\x20\x00",),
    )
    conn.commit()
    conn.close()

    # nt_msg.db with group_msg_table
    msgdb_path = db_dir / "nt_msg.db"
    conn = sqlite3.connect(msgdb_path)
    conn.execute(
        'CREATE TABLE group_msg_table ('
        '"40001" INTEGER, "40020" TEXT, "40021" TEXT, "40027" INTEGER, '
        '"40050" INTEGER, "40800" BLOB)'
    )

    # Insert text messages for group 100
    ts = 1759851722  # Oct 7 2025
    for i in range(5):
       inner = make_varint_field(45002, 1) + make_bytes_field(45101, f"消息{i}".encode("utf-8"))
       outer = make_bytes_field(40800, inner)
       conn.execute(
           'INSERT INTO group_msg_table VALUES (?, ?, ?, ?, ?, ?)',
            (7500000000 + i, "", "100", 999, ts + i, outer),
       )
    # Insert text messages for group 200
    for i in range(3):
       inner = make_varint_field(45002, 1) + make_bytes_field(45101, f"群2消息{i}".encode("utf-8"))
       outer = make_bytes_field(40800, inner)
       conn.execute(
           'INSERT INTO group_msg_table VALUES (?, ?, ?, ?, ?, ?)',
            (7600000000 + i, "", "200", 888, ts + i + 100, outer),
       )
    # Insert an image message (should be excluded from text output)
    inner = make_varint_field(45002, 2)
    outer = make_bytes_field(40800, inner)
    conn.execute(
       'INSERT INTO group_msg_table VALUES (?, ?, ?, ?, ?, ?)',
        (7700000000, "", "100", 999, ts + 10, outer),
    )
    conn.commit()
    conn.close()

    return db_dir


@pytest.fixture
def ntqq_fts_dbs(ntqq_dbs):
    """Add the searchable message database used by current NTQQ releases."""
    path = ntqq_dbs / "group_msg_fts.db"
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE group_msg_fts ('
        '"41701" TEXT, "41702" TEXT, "41703" TEXT, "41704" TEXT, '
        '"41705" TEXT, "41706" TEXT, "41707" TEXT, "41700" INTEGER PRIMARY KEY, '
        '"40001" INTEGER UNIQUE, "40050" INTEGER, "40003" INTEGER, '
        '"40010" INTEGER, "40021" TEXT, "40027" INTEGER, "40020" TEXT)'
    )
    ts = 1759851722
    rows = [
        (1, 8101, ts, "u_sender", "100", 100, "普通正文", "", "2", "1"),
        (2, 8102, ts + 1, "12345", "100", 100, "@成员 回复内容", "", "9", "33"),
        (3, 8103, ts + 2, "u_sender", "100", 100, "", "资料.pdf", "3", "1"),
        (4, 8104, ts + 3, "u_sender", "100", 100, "转发的聊天记录", "", "8", "0"),
        (5, 8105, ts + 4, "u_sender", "100", 100, "[分享]有用网站", "", "11", "0"),
        (6, 8201, ts + 100, "u_other", "200", 200, "另一个群", "", "2", "1"),
    ]
    conn.executemany(
        'INSERT INTO group_msg_fts('
        '"41700","40001","40050","40020","40021","40027",'
        '"41701","41702","41703","41704","41705","41706","41707") '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "", "", "")',
        rows,
    )
    conn.commit()
    conn.close()
    return ntqq_dbs


class TestDiscoverGroups:
    def test_discovery_prefers_searchable_database_activity(self, ntqq_fts_dbs):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        with sqlite3.connect(ntqq_fts_dbs / "group_msg_fts.db") as conn:
            conn.execute(
                'INSERT INTO group_msg_fts('
                '"41700","40001","40050","40020","40021","40027",'
                '"41701","41702","41703","41704","41705","41706","41707") '
                'VALUES (99, 8999, ?, "u_recent", "100", 100, '
                '"最近正文", "", "2", "1", "", "", "")',
                (int(now.timestamp()),),
            )

        groups = NTQQCollector(db_dir=ntqq_fts_dbs).discover_groups()

        group100 = next(group for group in groups if group["group_id"] == 100)
        group200 = next(group for group in groups if group["group_id"] == 200)
        assert group100["message_count_30d"] == 1
        assert group100["latest_message_at"] >= now - timedelta(seconds=2)
        assert group200["message_count_30d"] == 0

    def test_discovers_all_groups(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        groups = collector.discover_groups()
        ids = {g["group_id"] for g in groups}
        assert 100 in ids
        assert 200 in ids

    def test_group_has_name(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        groups = collector.discover_groups()
        group100 = next(g for g in groups if g["group_id"] == 100)
        assert group100["name"] == "测试群100"

    def test_message_count_only_includes_last_30_days(self, ntqq_dbs):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        recent_ts = int(now.timestamp())
        with sqlite3.connect(ntqq_dbs / "nt_msg.db") as conn:
            conn.execute(
                'INSERT INTO group_msg_table("40021", "40050") VALUES (?, ?)',
                ("100", recent_ts),
            )

        collector = NTQQCollector(db_dir=ntqq_dbs)
        groups = collector.discover_groups()

        group100 = next(g for g in groups if g["group_id"] == 100)
        group200 = next(g for g in groups if g["group_id"] == 200)
        assert group100["message_count_30d"] == 1
        assert group100["latest_message_at"] >= now - timedelta(seconds=2)
        assert group200["message_count_30d"] == 0


class TestCollect:
    def test_prefers_searchable_database_for_real_message_text(self, ntqq_fts_dbs):
        collector = NTQQCollector(db_dir=ntqq_fts_dbs)
        start = datetime(2025, 10, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2025, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        messages = list(collector.collect(100, start, end))

        assert [message.text for message in messages] == [
            "普通正文",
            "@成员 回复内容",
            "资料.pdf",
            "转发的聊天记录",
            "[分享]有用网站",
        ]
        assert [message.message_type for message in messages] == [
            "text",
            "reply",
            "file",
            "forward",
            "link",
        ]
        assert messages[0].sender_qq is None
        assert messages[0].content_json == {"sender_uid": "u_sender"}
        assert messages[1].sender_qq == 12345

    def test_metadata_scan_failure_is_not_silently_accepted(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)

        class BrokenConnection:
            def cursor(self):
                raise sqlite3.DatabaseError("corrupt page")

        with pytest.raises(CollectionIncompleteError, match="元数据"):
            collector._scan_metadata(BrokenConnection(), 100, 0, 1)

    def test_collects_text_messages(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        start = datetime(2025, 10, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2025, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        messages = list(collector.collect(100, start, end))
        # 5 text + 1 image (image returns "[图片]" placeholder text)
        assert len(messages) == 6
        text_msgs = [m for m in messages if m.message_type == "text"]
        assert len(text_msgs) == 5
        # Text content should contain "消息" (DESC order: newest first)
        assert all("消息" in m.text for m in text_msgs)

    def test_time_filtering(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        # Tight window that only includes first 2 text messages (23:42:02-23:42:03)
        start = datetime(2025, 10, 7, 23, 42, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2025, 10, 7, 23, 42, 3, tzinfo=ZoneInfo("Asia/Shanghai"))
        messages = list(collector.collect(100, start, end))
        assert len(messages) == 2

    def test_different_group(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        start = datetime(2025, 10, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2025, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        messages = list(collector.collect(200, start, end))
        assert len(messages) == 3
        assert all("群2消息" in m.text for m in messages)

    def test_sender_qq(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        start = datetime(2025, 10, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2025, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        messages = list(collector.collect(100, start, end))
        assert all(m.sender_qq == 999 for m in messages)

    def test_no_messages_outside_window(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        start = datetime(2020, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2020, 1, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
        messages = list(collector.collect(100, start, end))
        assert messages == []

    def test_unmapped_group_returns_empty(self, ntqq_dbs):
        collector = NTQQCollector(db_dir=ntqq_dbs)
        start = datetime(2025, 10, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2025, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        messages = list(collector.collect(999, start, end))
        assert messages == []
