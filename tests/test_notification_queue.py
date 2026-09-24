from datetime import datetime, timedelta, timezone

from qq_digest.archive import Archive
from qq_digest.notify.notifier import SendResult
from qq_digest.notify.queue import NotificationQueueProcessor


class FakeNotifier:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def send_report_notification(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_processor_schedules_exponential_retry_then_succeeds(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    send_id = archive.enqueue_notification(
        report_id=None,
        channel="qq_bot_private",
        recipient="openid-1",
        payload={"group_name": "开发群", "report_date": "2026-08-24", "pending_candidates": 1},
        now=now,
    )
    notifier = FakeNotifier([SendResult(False, "429"), SendResult(True)])
    processor = NotificationQueueProcessor(
        archive=archive,
        notifier=notifier,
        max_attempts=3,
        retry_base_seconds=60,
    )

    first = processor.process_due(now=now)
    assert first.failed == 1
    assert archive.due_notifications(now=now + timedelta(seconds=59)) == []

    second = processor.process_due(now=now + timedelta(seconds=60))
    assert second.sent == 1
    row = archive.connection.execute(
        "SELECT status FROM send_log WHERE send_id=?", (send_id,)
    ).fetchone()
    assert row["status"] == "success"


def test_processor_converts_unexpected_notifier_error_to_retry(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    archive.enqueue_notification(
        report_id=None,
        channel="qq_bot_private",
        recipient="openid-1",
        payload={},
        now=now,
    )
    processor = NotificationQueueProcessor(
        archive=archive,
        notifier=FakeNotifier([RuntimeError("socket closed")]),
        max_attempts=3,
        retry_base_seconds=60,
    )

    result = processor.process_due(now=now)

    row = archive.connection.execute("SELECT status,error FROM send_log").fetchone()
    assert result.failed == 1
    assert row["status"] == "pending_send"
    assert row["error"] == "socket closed"
