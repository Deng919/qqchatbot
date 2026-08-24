from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qq_digest.models import NormalizedMessage
from qq_digest.summary import Summarizer, SummaryResult, build_context


def message(msg_id, timestamp, text):
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=123,
        sender_qq=10001,
        timestamp=timestamp,
        text=text,
        collected_at=datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


class FakeAI:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.response


def valid_response():
    return {
        "group_id": 123,
        "main_topics": [{"topic": "工具推荐", "summary": "推荐了一个站点"}],
        "conclusions": ["站点适合归档"],
        "resources": [{"title": "站点", "link": "https://example.com"}],
        "tasks": [],
        "open_questions": [],
        "candidates": [
            {
                "type": "resource",
                "title": "站点",
                "link": "https://example.com",
                "content": "",
                "reason": "高质量教程站",
                "message_ids": ["m1"],
            }
        ],
    }


def test_build_context_limits_length_and_preserves_order():
    messages = [
        message("m1", datetime(2026, 8, 24, 9), "第一句"),
        message("m2", datetime(2026, 8, 24, 10), "第二句"),
    ]
    context = build_context(messages, max_chars=80)

    assert "第一句" in context
    assert "第二句" in context
    assert len(context) <= 80


def test_build_context_truncates_at_line_boundary():
    messages = [
        message("m1", datetime(2026, 8, 24, 9), "x" * 40),
        message("m2", datetime(2026, 8, 24, 10), "第二句"),
    ]
    second_line = "[10:00|10001] 第二句"
    context = build_context(messages, max_chars=len(second_line))

    assert context == second_line


def test_summarizer_returns_validated_result():
    ai = FakeAI(valid_response())
    summarizer = Summarizer(ai=ai, max_context_chars=1000)
    result = summarizer.summarize(
        group_id=123,
        group_name="测试群",
        window_start=datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        window_end=datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        messages=[
            message(
                "m1",
                datetime(2026, 8, 24, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
                "https://example.com",
            )
        ],
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    assert isinstance(result, SummaryResult)
    assert result.response.main_topics[0].topic == "工具推荐"


def test_summarizer_rejects_wrong_group():
    response = valid_response()
    response["group_id"] = 999
    summarizer = Summarizer(ai=FakeAI(response), max_context_chars=1000)

    with pytest.raises(ValueError, match="group_id"):
        summarizer.summarize(
            group_id=123,
            group_name="测试群",
            window_start=datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            window_end=datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
            messages=[],
            timezone=ZoneInfo("Asia/Shanghai"),
        )
