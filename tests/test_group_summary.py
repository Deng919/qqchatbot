from datetime import datetime
from zoneinfo import ZoneInfo

import qq_digest.group_summary as group_summary
from qq_digest.group_summary import GroupSummaryBuilder, summary_input_fingerprint
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.summary import SummaryResponse, SummaryResult


class StubSummarizer:
    def __init__(self):
        self.last_kwargs = None

    def summarize(self, **kwargs):
        self.last_kwargs = kwargs
        return SummaryResult(
            response=SummaryResponse.model_validate(
                {
                    "group_id": kwargs["group_id"],
                    "overview": "群内确认了工具回退方案。",
                    "main_topics": [
                        {"topic": "工具推荐", "summary": "适合处理日志"}
                    ],
                    "conclusions": ["结论"],
                    "resources": [
                        {
                            "title": "站点",
                            "url": "https://example.com",
                            "description": "官方文档",
                        }
                    ],
                    "tasks": [
                        {"owner": "小王", "description": "试用", "deadline": "周五"}
                    ],
                    "open_questions": [],
                    "candidates": [
                        {
                            "type": "resource",
                            "title": "站点",
                            "link": "https://example.com",
                            "reason": "有价值",
                            "message_ids": ["m1"],
                        }
                    ],
                }
            ),
            deterministic={"links": [], "files": [], "todos": []},
            context_chars=100,
            context_truncated=True,
            source_messages=3,
            included_messages=2,
            discarded_messages=1,
        )


def make_message() -> NormalizedMessage:
    now = datetime(2026, 9, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    return NormalizedMessage(
        msg_id="m1",
        group_id=123,
        sender_qq=1,
        timestamp=now,
        text="站点原文",
        collected_at=now,
    )


def test_group_summary_builder_builds_range_content_and_candidate_draft():
    timezone = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 9, 1, tzinfo=timezone)
    end = datetime(2026, 9, 8, tzinfo=timezone)
    summarizer = StubSummarizer()
    artifact = GroupSummaryBuilder(summarizer).build(
        group=GroupConfig(group_id=123, name="测试群"),
        window_start=start,
        window_end=end,
        report_date="2026-09-01 至 2026-09-07",
        candidate_date="2026-09-07",
        messages=[make_message()],
        timezone=timezone,
        knowledge_base="",
        report_kind="range",
    )

    assert "# 测试群范围摘要" in artifact.markdown
    assert "## 今日概览\n群内确认了工具回退方案。" in artifact.markdown
    assert "template" not in summarizer.last_kwargs
    assert "- 日期范围：2026-09-01 至 2026-09-07" in artifact.markdown
    assert "## 数据范围" in artifact.markdown
    assert "仅总结实际纳入的 2 条消息，部分消息因长度限制未包含" in artifact.markdown
    assert "- 时间窗：2026-09-01 00:00 至 2026-09-08 00:00" in artifact.markdown
    assert artifact.payload["diagnostics"] == {
        "source_messages": 3,
        "included_messages": 2,
        "discarded_messages": 1,
        "context_truncated": True,
        "context_chars": 100,
        "extracted": {"links": [], "files": [], "todos": []},
    }
    assert artifact.candidate_kwargs[0]["created_date"] == "2026-09-07"
    assert artifact.candidate_kwargs[0]["excerpt"] == "站点原文"
    assert artifact.source_message_count == 1


def test_group_summary_builder_keeps_daily_heading():
    timezone = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 9, 1, tzinfo=timezone)
    artifact = GroupSummaryBuilder(StubSummarizer()).build(
        group=GroupConfig(group_id=123, name="测试群"),
        window_start=start,
        window_end=start.replace(hour=22),
        report_date="2026-09-01",
        candidate_date="2026-09-01",
        messages=[make_message()],
        timezone=timezone,
        knowledge_base="",
        report_kind="daily",
    )

    assert "# 测试群日报" in artifact.markdown
    assert "- 日期：2026-09-01" in artifact.markdown
    assert "## 数据范围" in artifact.markdown


def test_summary_fingerprint_ignores_legacy_template_value():
    timezone = ZoneInfo("Asia/Shanghai")
    common = {
        "report_kind": "daily",
        "messages": [make_message()],
        "timezone": timezone,
        "knowledge_base": "",
        "max_context_chars": 1000,
    }

    concise = summary_input_fingerprint(
        group=GroupConfig(group_id=123, name="测试群", template="concise"),
        **common,
    )
    detailed = summary_input_fingerprint(
        group=GroupConfig(group_id=123, name="测试群", template="detailed"),
        **common,
    )

    assert concise == detailed


def test_summary_fingerprint_tracks_report_format_version(monkeypatch):
    kwargs = {
        "group": GroupConfig(group_id=123, name="测试群"),
        "report_kind": "daily",
        "messages": [make_message()],
        "timezone": ZoneInfo("Asia/Shanghai"),
        "knowledge_base": "",
        "max_context_chars": 1000,
    }
    original = summary_input_fingerprint(**kwargs)

    monkeypatch.setattr(
        group_summary, "REPORT_FORMAT_VERSION", group_summary.REPORT_FORMAT_VERSION + 1
    )

    assert summary_input_fingerprint(**kwargs) != original
