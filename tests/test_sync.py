from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from qq_digest.archive import Archive
from qq_digest.config import AIConfig, Config, NTQQConfig, SecurityConfig
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.sync import run_message_sync


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
