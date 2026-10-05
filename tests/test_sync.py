from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from qq_digest.archive import Archive
from qq_digest.config import AIConfig, Config, NTQQConfig, SecurityConfig
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.sync import run_message_sync
from qq_digest.pipeline import DailyPipeline
import pytest


class RecordingCollector:
    def __init__(self, messages):
        self.messages = messages
        self.windows = []

    def collect(self, group_id, start, end):
        self.windows.append((group_id, start, end))
        return [
            message
            for message in self.messages
            if message.group_id == group_id and start <= message.timestamp <= end
        ]


class FailingCollector:
    def collect(self, group_id, start, end):
        raise RuntimeError("本地消息库不可读")


@pytest.mark.parametrize("previous_days", [None, 7])
def test_daily_collection_preserves_full_history_sync_window(tmp_path, previous_days):
    now = datetime(2026, 10, 5, 22, tzinfo=ZoneInfo("Asia/Shanghai"))
    config = Config(
        data_dir=tmp_path, archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports", knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="unused"),
        ai=AIConfig(base_url="https://example.com", model="unused", api_key_env="TEST_KEY"),
        ntqq=NTQQConfig(enabled=True, db_dir=str(tmp_path / "unused")),
    )
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="history", collection_window_days=30)])
    previous = now - timedelta(days=previous_days) if previous_days else None
    if previous is not None:
        archive.mark_sync(group_id=123, last_timestamp=previous)
    collector = RecordingCollector([])
    DailyPipeline(archive=archive, collector=collector, ai_client=None,
                  report_dir=config.report_dir, max_context_chars=1000,
                  timezone_name="Asia/Shanghai").run_daily(now)
    state = archive.connection.execute("SELECT * FROM sync_state WHERE group_id=123").fetchone()
    assert state["last_success_at"] is not None
    assert state["status"] == ("active" if previous else "report_collection_completed")
    assert state["last_timestamp"] == (previous.astimezone(ZoneInfo("UTC")).isoformat() if previous else None)
    archive.close()
    assert run_message_sync(config=config, now=now, refresh=False, collector=collector).success
    expected = previous - timedelta(hours=1) if previous else now - timedelta(days=30)
    assert collector.windows[1][1] == expected


def test_periodic_sync_uses_overlap_and_archives_messages(tmp_path):
    now = datetime(2026, 8, 30, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    previous_sync = now - timedelta(minutes=10)
    config = Config(
        data_dir=tmp_path,
        archive_path=tmp_path / "archive" / "archive.sqlite",
        report_dir=tmp_path / "reports",
        knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="x" * 32),
        ai=AIConfig(base_url="https://example.com/v1", model="test", api_key_env="TEST_KEY"),
        ntqq=NTQQConfig(enabled=True, qq_number=1, db_dir=str(tmp_path / "ntqq")),
    )
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    archive.mark_sync(group_id=123, last_timestamp=previous_sync)
    archive.connection.close()
    message = NormalizedMessage(
        msg_id="overlap-message",
        group_id=123,
        sender_qq=1,
        timestamp=previous_sync - timedelta(minutes=20),
        text="重叠窗口中的消息",
        collected_at=now,
    )
    collector = RecordingCollector([message])

    result = run_message_sync(
        config=config,
        now=now,
        refresh=False,
        collector=collector,
    )

    assert result.success is True
    assert result.messages_inserted == 1
    assert collector.windows[0][1] == previous_sync - timedelta(minutes=60)
    archive = Archive.open(config.archive_path)
    assert archive.count_messages(123) == 1
    state = archive.connection.execute(
        "SELECT status, last_timestamp FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert state["status"] == "active"
    assert datetime.fromisoformat(state["last_timestamp"]) == now.astimezone(ZoneInfo("UTC"))


def test_periodic_sync_records_group_failure_reason(tmp_path):
    now = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    config = Config(
        data_dir=tmp_path,
        archive_path=tmp_path / "archive" / "archive.sqlite",
        report_dir=tmp_path / "reports",
        knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="x" * 32),
        ai=AIConfig(base_url="https://example.com/v1", model="test", api_key_env="TEST_KEY"),
        ntqq=NTQQConfig(enabled=True, qq_number=1, db_dir=str(tmp_path / "ntqq")),
    )
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    archive.close()

    result = run_message_sync(config=config, now=now, refresh=False, collector=FailingCollector())

    assert result.success is False
    archive = Archive.open(config.archive_path)
    state = archive.connection.execute(
        "SELECT status, error FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert state["status"] == "adapter_incompatible"
    assert state["error"] == "本地消息库不可读"


def test_periodic_sync_retry_records_original_job(tmp_path):
    now = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    config = Config(
        data_dir=tmp_path, archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports", knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="x" * 32),
        ai=AIConfig(base_url="https://example.com/v1", model="test", api_key_env="TEST_KEY"),
        ntqq=NTQQConfig(enabled=True, qq_number=1, db_dir=str(tmp_path / "ntqq")),
    )
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    original = archive.start_job("message_sync")
    archive.finish_job(original, "failed", "数据库不可读")
    archive.close()
    run_message_sync(
        config=config, now=now, refresh=False, collector=RecordingCollector([]),
        retry_of_job_id=original,
    )
    archive = Archive.open(config.archive_path)
    row = archive.connection.execute(
        "SELECT retry_of_job_id,status FROM jobs ORDER BY job_id DESC LIMIT 1"
    ).fetchone()
    assert row["retry_of_job_id"] == original
    assert row["status"] == "success"
