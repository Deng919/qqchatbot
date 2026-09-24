from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig
from tests.factories import make_message


def test_ingest_is_idempotent(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    result = archive.ingest([make_message()])
    result_again = archive.ingest([make_message()])

    assert result.inserted == 1
    assert result_again.inserted == 0
    assert archive.count_messages(group_id=123) == 1


def test_ingest_accepts_out_of_order_messages(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    base = datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    messages = [make_message("later", base + timedelta(hours=1)), make_message("earlier", base)]
    result = archive.ingest(messages)

    assert result.inserted == 2
    assert [m.msg_id for m in archive.messages_between(123, base, base + timedelta(hours=1))] == [
        "earlier",
        "later",
    ]


def test_ingest_rejects_duplicate_msg_id_in_same_batch(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")

    try:
        archive.ingest([make_message(), make_message()])
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert archive.count_messages(group_id=123) == 0


def test_archive_records_knowledge_item_idempotently(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.connection.execute(
        """
        INSERT INTO candidates(
            group_id, message_ids, created_date, candidate_type, title,
            link, content, reason, excerpt, status, created_at, updated_at
        ) VALUES (
            123, '["m1"]', '2026-08-24', 'resource', '站点',
            'https://example.com', '', '高质量', '摘录', 'pending',
            '2026-08-24T14:00:00+00:00', '2026-08-24T14:00:00+00:00'
        )
        """
    )
    archive.connection.commit()
    candidate_id = int(
        archive.connection.execute("SELECT candidate_id FROM candidates").fetchone()[
            "candidate_id"
        ]
    )

    archive.record_knowledge_item(
        item_id="k1",
        candidate_id=candidate_id,
        markdown_path="knowledge/resources.md",
    )
    archive.record_knowledge_item(
        item_id="k1",
        candidate_id=candidate_id,
        markdown_path="knowledge/resources.md",
    )

    count = archive.connection.execute(
        "SELECT COUNT(*) AS total FROM knowledge_items"
    ).fetchone()["total"]
    assert count == 1


def test_delete_group_purges_all_related_database_records(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    message = make_message(
        msg_id="m-delete",
        timestamp=datetime(2026, 8, 24, 10, tzinfo=ZoneInfo("UTC")),
    )
    archive.ingest([message])
    archive.mark_sync(
        group_id=123,
        last_timestamp=datetime(2026, 8, 24, 10, tzinfo=ZoneInfo("UTC")),
    )
    with archive.transaction():
        candidate_id = archive.connection.execute(
            """
            INSERT INTO candidates(
                group_id,message_ids,created_date,candidate_type,title,link,content,
                reason,excerpt,status,created_at,updated_at
            ) VALUES (123,'[\"m-delete\"]','2026-08-24','resource','标题','','',
                      '原因','','confirmed','now','now')
            """
        ).lastrowid
        report_id = archive.connection.execute(
            """
            INSERT INTO reports(
                group_id,report_date,markdown_path,json_path,candidate_ids,created_at
            ) VALUES (123,'2026-08-24','report.md','report.json',?,'now')
            """,
            (f"[{candidate_id}]",),
        ).lastrowid
        archive.connection.execute(
            "INSERT INTO knowledge_items VALUES ('item',?,'knowledge.md','now')",
            (candidate_id,),
        )
        archive.connection.execute(
            "INSERT INTO send_log(report_id,channel,status,error,attempted_at) VALUES (?,'qq','success','','now')",
            (report_id,),
        )

    result = archive.delete_group(123)

    assert result.group_id == 123
    assert result.report_paths == ("report.md", "report.json")
    for table in (
        "groups",
        "messages",
        "sync_state",
        "reports",
        "candidates",
        "knowledge_items",
        "send_log",
    ):
        assert archive.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_notification_queue_persists_and_retries(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    send_id = archive.enqueue_notification(
        report_id=None,
        channel="qq_bot_private",
        recipient="openid-1",
        payload={"group_name": "开发群", "report_date": "2026-08-24", "pending_candidates": 2},
        now=now,
    )

    due = archive.due_notifications(now=now)
    assert len(due) == 1
    assert due[0].send_id == send_id
    assert due[0].payload["group_name"] == "开发群"
    assert archive.connection.execute(
        "SELECT status FROM send_log WHERE send_id=?", (send_id,)
    ).fetchone()["status"] == "pending_send"

    archive.mark_notification_retry(
        send_id,
        error="429",
        next_attempt_at=now + timedelta(minutes=1),
        max_attempts=3,
        now=now,
    )
    assert archive.due_notifications(now=now) == []
    assert len(archive.due_notifications(now=now + timedelta(minutes=1))) == 1

    archive.mark_notification_success(send_id, now=now + timedelta(minutes=1))
    assert archive.due_notifications(now=now + timedelta(days=1)) == []
    row = archive.connection.execute(
        "SELECT status, retry_count FROM send_log WHERE send_id=?", (send_id,)
    ).fetchone()
    assert row["status"] == "success"
    assert row["retry_count"] == 1


def test_notification_queue_stops_at_attempt_limit(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    send_id = archive.enqueue_notification(
        report_id=None,
        channel="qq_bot_private",
        recipient="openid-1",
        payload={},
        now=now,
    )

    for attempt in range(3):
        archive.mark_notification_retry(
            send_id,
            error="网络错误",
            next_attempt_at=now,
            max_attempts=3,
            now=now,
        )

    row = archive.connection.execute(
        "SELECT status, retry_count FROM send_log WHERE send_id=?", (send_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["retry_count"] == 3
    assert archive.due_notifications(now=now) == []


def test_notification_claim_is_atomic_and_recovers_expired_lease(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    send_id = archive.enqueue_notification(
        report_id=None,
        channel="qq_bot_private",
        recipient="openid-1",
        payload={},
        now=now,
    )

    first = archive.claim_due_notifications(now=now, lease_seconds=300)
    second = archive.claim_due_notifications(now=now, lease_seconds=300)
    recovered = archive.claim_due_notifications(
        now=now + timedelta(seconds=301), lease_seconds=300
    )

    assert [item.send_id for item in first] == [send_id]
    assert second == []
    assert [item.send_id for item in recovered] == [send_id]


def test_archive_migrates_manual_reports_table(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")

    columns = {
        row["name"]
        for row in archive.connection.execute(
            "PRAGMA table_info(manual_reports)"
        ).fetchall()
    }

    assert {
        "manual_report_id",
        "group_id",
        "start_date",
        "end_date",
        "detail_mode",
        "effective_template",
        "markdown_path",
        "json_path",
        "candidate_ids",
        "input_fingerprint",
        "source_message_count",
        "created_at",
        "updated_at",
    } <= columns


def test_daily_report_stores_effective_template(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])

    report_id = archive.record_report(
        group_id=123,
        report_date="2026-09-01",
        effective_template="detailed",
        markdown_path=tmp_path / "daily.md",
        json_path=tmp_path / "daily.json",
        candidate_ids=[],
    )

    assert report_id > 0
    report = archive.report_for(123, "2026-09-01")
    assert report["effective_template"] == "detailed"


def test_messages_in_window_excludes_right_boundary(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    start = datetime(2026, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start + timedelta(days=1)
    archive.ingest(
        [
            make_message("start", start),
            make_message("inside", end - timedelta(seconds=1)),
            make_message("next-day", end),
        ]
    )

    assert [
        message.msg_id for message in archive.messages_in_window(123, start, end)
    ] == ["start", "inside"]


def test_manual_report_upsert_keeps_identity_and_created_at(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])

    first_id = archive.record_manual_report(
        group_id=123,
        start_date="2026-09-01",
        end_date="2026-09-07",
        detail_mode="group",
        effective_template="concise",
        markdown_path=tmp_path / "first.md",
        json_path=tmp_path / "first.json",
        candidate_ids=[1],
        input_fingerprint="first",
        source_message_count=3,
    )
    first = archive.manual_report_for(
        123, "2026-09-01", "2026-09-07", "group"
    )
    second_id = archive.record_manual_report(
        group_id=123,
        start_date="2026-09-01",
        end_date="2026-09-07",
        detail_mode="group",
        effective_template="detailed",
        markdown_path=tmp_path / "second.md",
        json_path=tmp_path / "second.json",
        candidate_ids=[2],
        input_fingerprint="second",
        source_message_count=4,
    )
    second = archive.manual_report_by_id(second_id)

    assert first_id == second_id
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["effective_template"] == "detailed"
    assert second["input_fingerprint"] == "second"
    assert second["source_message_count"] == 4


def test_delete_group_includes_manual_report_files(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    archive.record_manual_report(
        group_id=123,
        start_date="2026-09-01",
        end_date="2026-09-07",
        detail_mode="group",
        effective_template="concise",
        markdown_path=tmp_path / "range.md",
        json_path=tmp_path / "range.json",
        candidate_ids=[],
        input_fingerprint="fingerprint",
        source_message_count=1,
    )

    result = archive.delete_group(123)

    assert result.report_paths == (
        str(tmp_path / "range.md"),
        str(tmp_path / "range.json"),
    )
    assert result.deleted["manual_reports"] == 1
