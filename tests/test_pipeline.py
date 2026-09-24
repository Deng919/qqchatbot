from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from types import SimpleNamespace

from qq_digest.archive import Archive
from qq_digest.collector.fixture import FixtureCollector
from qq_digest.models import GroupConfig
from qq_digest.models import NormalizedMessage
from qq_digest.pipeline import DailyPipeline
from qq_digest.notify.notifier import SendResult
from qq_digest.summary import SummaryResponse, SummaryResult


class FakeAI:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return {
            "group_id": 123,
            "main_topics": [{"topic": "资源分享", "summary": "分享了教程站点"}],
            "conclusions": [],
            "resources": [{"title": "站点", "link": "https://example.com"}],
            "tasks": [],
            "open_questions": [],
            "candidates": [
                {
                    "type": "resource",
                    "title": "站点",
                    "link": "https://example.com",
                    "content": "",
                    "reason": "高质量教程",
                    "message_ids": ["fixture-123"],
                }
            ],
        }


class FailingAI:
    def chat(self, messages):
        raise RuntimeError("temporary AI outage")


class CountingAI:
    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        raise AssertionError("空消息群不应调用 AI")


class EmptyCollector:
    def collect(self, group_id, start, end):
        return []


class BrokenCollector:
    def collect(self, group_id, start, end):
        raise RuntimeError("corrupt page")


class MutableCollector:
    def __init__(self, messages):
        self.messages = messages

    def collect(self, group_id, start, end):
        return [
            message
            for message in self.messages
            if message.group_id == group_id and start <= message.timestamp <= end
        ]


class SuccessfulNotifier:
    def __init__(self):
        self.config = SimpleNamespace(
            app_id="app", allowed_openids=["openid-1"], max_retries=3,
            retry_base_seconds=60,
        )
        self.calls = []

    def send_report_notification(self, **kwargs):
        self.calls.append(kwargs)
        return SendResult(True)


class SelectiveSummarizer:
    def __init__(self, failing_ids):
        self.failing_ids = set(failing_ids)
        self.calls = []

    def summarize(self, **kwargs):
        group_id = kwargs["group_id"]
        self.calls.append(group_id)
        if group_id in self.failing_ids:
            raise RuntimeError("temporary AI outage")
        return SummaryResult(
            response=SummaryResponse(
                group_id=group_id,
                main_topics=[],
                conclusions=[],
                resources=[],
                tasks=[],
                open_questions=[],
                candidates=[],
            ),
            deterministic={"links": [], "files": [], "todos": []},
            context_chars=10,
            context_truncated=False,
            source_messages=1,
            included_messages=1,
            discarded_messages=0,
        )


def make_pipeline(tmp_path: Path, now: datetime) -> tuple[DailyPipeline, Archive]:
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    return (
        DailyPipeline(
            archive=archive,
            collector=FixtureCollector(now=now),
            ai_client=FakeAI(),
            report_dir=tmp_path / "reports",
            max_context_chars=1000,
            timezone_name="Asia/Shanghai",
            window_mode="today",
        ),
        archive,
    )


def make_two_group_pipeline(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups(
        [
            GroupConfig(group_id=123, name="群一"),
            GroupConfig(group_id=456, name="群二"),
        ]
    )
    collector = MutableCollector(
        [
            NormalizedMessage(
                msg_id="one",
                group_id=123,
                sender_qq=1,
                timestamp=now.replace(hour=9),
                text="第一群消息",
                collected_at=now,
            ),
            NormalizedMessage(
                msg_id="two",
                group_id=456,
                sender_qq=2,
                timestamp=now.replace(hour=10),
                text="第二群消息",
                collected_at=now,
            ),
        ]
    )
    pipeline = DailyPipeline(
        archive=archive,
        collector=collector,
        ai_client=FakeAI(),
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )
    return pipeline, archive, now


def test_run_daily_continues_after_one_group_summary_fails(tmp_path):
    pipeline, archive, now = make_two_group_pipeline(tmp_path)
    pipeline.summarizer = SelectiveSummarizer({123})

    result = pipeline.run_daily(now)

    assert result.status == "partial_success"
    assert [item.group_id for item in result.failed_groups] == [123]
    assert result.succeeded_groups == [456]
    assert archive.report_for(123, "2026-08-24") is None
    assert archive.report_for(456, "2026-08-24") is not None
    assert (
        archive.connection.execute(
            "SELECT status FROM jobs ORDER BY job_id DESC"
        ).fetchone()["status"]
        == "partial_success"
    )


def test_partial_retry_only_calls_groups_without_current_report(tmp_path):
    pipeline, archive, now = make_two_group_pipeline(tmp_path)
    selective = SelectiveSummarizer({123})
    pipeline.summarizer = selective

    first = pipeline.run_daily(now)
    assert first.status == "partial_success"
    selective.calls.clear()
    selective.failing_ids.clear()

    second = pipeline.run_daily(now)

    assert second.status == "success"
    assert selective.calls == [123]
    assert archive.report_for(123, "2026-08-24") is not None
    assert archive.report_for(456, "2026-08-24") is not None
    statuses = [
        row["status"]
        for row in archive.connection.execute("SELECT status FROM jobs ORDER BY job_id")
    ]
    assert statuses == ["partial_success", "success"]


def test_run_daily_creates_report_and_candidate(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline, archive = make_pipeline(tmp_path, now)

    result = pipeline.run_daily(now)

    assert result.groups_processed == 1
    assert result.report_paths[0].markdown.exists()
    assert result.candidate_ids == [1]
    assert result.messages_inserted == 1
    assert len(pipeline.candidates.pending()) == 1
    prompt = "\n".join(item["content"] for item in pipeline.summarizer.ai.calls[0])
    assert "模板:" not in prompt
    report = archive.report_for(123, "2026-08-24")
    assert report["effective_template"] == "adaptive"
    job = archive.connection.execute("SELECT * FROM jobs").fetchone()
    assert job["status"] == "success"


def test_run_daily_skips_ai_and_report_when_group_has_no_messages(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="空群")])
    ai = CountingAI()
    pipeline = DailyPipeline(
        archive=archive,
        collector=EmptyCollector(),
        ai_client=ai,
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )

    result = pipeline.run_daily(now)

    assert result.groups_processed == 1
    assert result.report_paths == []
    assert ai.calls == 0
    assert archive.report_for(123, "2026-08-24") is None


def test_run_daily_is_idempotent(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline, archive = make_pipeline(tmp_path, now)

    first = pipeline.run_daily(now)
    second = pipeline.run_daily(now)

    assert second.candidate_ids == first.candidate_ids
    assert len(pipeline.candidates.pending()) == 1
    reports = archive.connection.execute("SELECT * FROM reports").fetchall()
    assert len(reports) == 1


def test_run_daily_queues_notification_with_report_reference(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline, archive = make_pipeline(tmp_path, now)
    notifier = SuccessfulNotifier()
    pipeline.notifier = notifier

    pipeline.run_daily(now)

    row = archive.connection.execute("SELECT * FROM send_log").fetchone()
    report = archive.connection.execute("SELECT report_id FROM reports").fetchone()
    assert row["report_id"] == report["report_id"]
    assert row["recipient"] == "openid-1"
    assert row["status"] == "success"
    assert notifier.calls[0]["group_name"] == "测试群"


def test_collection_failure_creates_visible_sync_state_without_advancing_cursor(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    pipeline = DailyPipeline(
        archive=archive,
        collector=BrokenCollector(),
        ai_client=FakeAI(),
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )

    result = pipeline.run_daily(now)

    state = archive.connection.execute(
        "SELECT status, last_timestamp FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert result.groups_processed == 0
    assert result.status == "partial_success"
    assert result.failed_groups[0].stage == "collection"
    assert state["status"] == "adapter_incompatible"
    assert state["last_timestamp"] is None


def test_same_day_report_regenerates_when_new_messages_arrive(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first_message = NormalizedMessage(
        msg_id="fixture-123",
        group_id=123,
        sender_qq=1,
        timestamp=now.replace(hour=9),
        text="第一条",
        collected_at=now,
    )
    collector = MutableCollector([first_message])
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    pipeline = DailyPipeline(
        archive=archive,
        collector=collector,
        ai_client=FakeAI(),
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )
    pipeline.run_daily(now)
    collector.messages.append(
        NormalizedMessage(
            msg_id="late-123",
            group_id=123,
            sender_qq=2,
            timestamp=now.replace(hour=20),
            text="晚到消息",
            collected_at=now,
        )
    )

    second = pipeline.run_daily(now)

    report = archive.report_for(123, "2026-08-24")
    assert len(second.report_paths) == 1
    assert report["source_message_count"] == 2
    assert report["input_fingerprint"]


def test_failed_regeneration_preserves_existing_pending_candidates(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first_message = NormalizedMessage(
        msg_id="fixture-123",
        group_id=123,
        sender_qq=1,
        timestamp=now.replace(hour=9),
        text="第一条",
        collected_at=now,
    )
    collector = MutableCollector([first_message])
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    pipeline = DailyPipeline(
        archive=archive,
        collector=collector,
        ai_client=FakeAI(),
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )
    pipeline.run_daily(now)
    collector.messages.append(
        NormalizedMessage(
            msg_id="late-123",
            group_id=123,
            sender_qq=2,
            timestamp=now.replace(hour=20),
            text="晚到消息",
            collected_at=now,
        )
    )
    pipeline.summarizer.ai = FailingAI()

    result = pipeline.run_daily(now)

    assert result.status == "partial_success"
    assert [item.group_id for item in result.failed_groups] == [123]
    assert len(pipeline.candidates.pending()) == 1


def test_candidate_failure_preserves_old_candidates_and_does_not_report_success(
    tmp_path, monkeypatch
):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    collector = MutableCollector(
        [
            NormalizedMessage(
                msg_id="fixture-123",
                group_id=123,
                sender_qq=1,
                timestamp=now.replace(hour=9),
                text="第一条",
                collected_at=now,
            )
        ]
    )
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    pipeline = DailyPipeline(
        archive=archive,
        collector=collector,
        ai_client=FakeAI(),
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )
    first = pipeline.run_daily(now)
    old_markdown = first.report_paths[0].markdown.read_text(encoding="utf-8")
    collector.messages.append(
        NormalizedMessage(
            msg_id="late-123",
            group_id=123,
            sender_qq=2,
            timestamp=now.replace(hour=20),
            text="晚到消息",
            collected_at=now,
        )
    )

    def fail_candidate(**kwargs):
        raise RuntimeError("candidate insert failed")

    monkeypatch.setattr(
        pipeline.candidates, "create_in_transaction", fail_candidate
    )

    result = pipeline.run_daily(now)

    assert result.status == "partial_success"
    assert result.report_paths == []
    assert result.candidate_ids == []
    assert len(pipeline.candidates.pending()) == 1
    assert first.report_paths[0].markdown.read_text(encoding="utf-8") == old_markdown


def test_ai_failure_does_not_prevent_other_groups_from_being_collected(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups(
        [
            GroupConfig(group_id=123, name="群一"),
            GroupConfig(group_id=456, name="群二"),
        ]
    )
    collector = MutableCollector(
        [
            NormalizedMessage(
                msg_id="one",
                group_id=123,
                sender_qq=1,
                timestamp=now.replace(hour=9),
                text="第一群消息",
                collected_at=now,
            ),
            NormalizedMessage(
                msg_id="two",
                group_id=456,
                sender_qq=2,
                timestamp=now.replace(hour=10),
                text="第二群消息",
                collected_at=now,
            ),
        ]
    )
    pipeline = DailyPipeline(
        archive=archive,
        collector=collector,
        ai_client=FailingAI(),
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )

    result = pipeline.run_daily(now)

    assert result.status == "partial_success"
    assert [item.group_id for item in result.failed_groups] == [123, 456]
    assert archive.count_messages(123) == 1
    assert archive.count_messages(456) == 1
