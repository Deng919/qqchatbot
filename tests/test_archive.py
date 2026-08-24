from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from qq_digest.archive import Archive
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
