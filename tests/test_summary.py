from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from qq_digest.models import NormalizedMessage
from qq_digest.prompt_builder import build_system_prompt
from qq_digest.summary import (
    CandidateOutput,
    Resource,
    Summarizer,
    SummaryResponse,
    SummaryResult,
    build_context,
    build_context_window,
)


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
        "resources": [{"title": "站点", "url": "https://example.com"}],
        "tasks": [{"owner": "小王", "description": "整理教程", "deadline": "明天"}],
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


def test_adaptive_prompt_requests_rich_evidence_bound_topics():
    prompt = build_system_prompt("general")

    assert "今日概览" in prompt
    assert "通常 3 至 8 个" in prompt
    assert "背景、主要观点、结论或当前状态" in prompt
    assert "两条及以上有信息量的往来" in prompt
    assert "为了凑数" in prompt
    assert "summary（不超过 30 字）" not in prompt


def test_specialized_prompts_preserve_important_single_message_exception():
    tech_prompt = build_system_prompt("tech")
    resource_prompt = build_system_prompt("resource")

    assert "单条完整的版本发布、故障通告或安全公告" in tech_prompt
    assert "完整的重要事件或公告除外" in resource_prompt


def test_summary_response_overview_is_backward_compatible():
    payload = valid_response()

    assert SummaryResponse.model_validate(payload).overview == ""
    payload["overview"] = "当天围绕模型额度与客户端兼容性展开讨论。"
    assert SummaryResponse.model_validate(payload).overview.startswith("当天")


def test_summarizer_does_not_send_template_to_model():
    ai = FakeAI({**valid_response(), "overview": "概览"})
    summarizer = Summarizer(ai=ai, max_context_chars=1000)

    summarizer.summarize(
        group_id=123,
        group_name="测试群",
        category="general",
        window_start=datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        window_end=datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        messages=[
            message(
                "m1",
                datetime(2026, 8, 24, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
                "有效消息",
            )
        ],
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    combined = "\n".join(item["content"] for item in ai.calls[0])
    assert "模板:" not in combined


def test_build_context_limits_length_and_preserves_order():
    messages = [
        message("m1", datetime(2026, 8, 24, 9), "第一句"),
        message("m2", datetime(2026, 8, 24, 10), "第二句"),
    ]
    context = build_context(messages, max_chars=80)

    assert "第一句" in context
    assert "第二句" in context
    assert "[m1|2026-08-24 09:00|10001]" in context
    assert len(context) <= 80


def test_build_context_truncates_at_line_boundary():
    messages = [
        message("m1", datetime(2026, 8, 24, 9), "x" * 40),
        message("m2", datetime(2026, 8, 24, 10), "第二句"),
    ]
    second_line = "[m2|2026-08-24 10:00|10001] 第二句"
    context = build_context(messages, max_chars=len(second_line))

    assert context == second_line


def test_build_context_window_reports_truncation():
    messages = [
        message("m1", datetime(2026, 8, 24, 9), "x" * 40),
        message("m2", datetime(2026, 8, 24, 10), "第二句"),
    ]
    second_line = "[m2|2026-08-24 10:00|10001] 第二句"

    window = build_context_window(messages, max_chars=len(second_line))

    assert window.text == second_line
    assert window.chars == len(second_line)
    assert window.truncated is True


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
    assert result.response.resources[0].url == "https://example.com"
    assert result.response.tasks[0].owner == "小王"
    assert result.response.tasks[0].description == "整理教程"
    assert result.context_truncated is False


def test_summarizer_normalizes_integer_candidate_message_ids():
    message_id = "7684528150456769779"
    response = valid_response()
    response["candidates"][0]["message_ids"] = [int(message_id)]
    summarizer = Summarizer(ai=FakeAI(response), max_context_chars=1000)

    result = summarizer.summarize(
        group_id=123,
        group_name="测试群",
        window_start=datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        window_end=datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        messages=[
            message(
                message_id,
                datetime(2026, 8, 24, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
                "分享一个值得保存的站点",
            )
        ],
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    assert result.response.candidates[0].message_ids == [message_id]


@pytest.mark.parametrize("invalid_id", [True, 1.5, {"id": 1}])
def test_candidate_message_ids_still_reject_other_non_strings(invalid_id):
    candidate = valid_response()["candidates"][0]
    candidate["message_ids"] = [invalid_id]

    with pytest.raises(ValidationError):
        CandidateOutput.model_validate(candidate)


def test_summarizer_includes_group_keywords_in_user_prompt():
    ai = FakeAI(valid_response())
    summarizer = Summarizer(ai=ai, max_context_chars=1000)

    summarizer.summarize(
        group_id=123,
        group_name="测试群",
        keywords=["网站", "踩坑经验"],
        window_start=datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        window_end=datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        messages=[
            message(
                "m1",
                datetime(2026, 8, 24, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
                "分享一个网站",
            )
        ],
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    user_prompt = ai.calls[0][1]["content"]
    assert "群重点关键词: 网站、踩坑经验" in user_prompt
    assert "不得因为命中关键词而降低候选标准" in user_prompt


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
            messages=[
                message(
                    "m1",
                    datetime(2026, 8, 24, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
                    "有效讨论",
                )
            ],
            timezone=ZoneInfo("Asia/Shanghai"),
        )


def test_resource_description_survives_validation():
    resource = Resource.model_validate(
        {
            "title": "文档",
            "url": "https://example.com",
            "description": "部署指南",
        }
    )

    assert resource.description == "部署指南"


def test_summarizer_builds_context_from_cleaned_messages():
    timezone = ZoneInfo("Asia/Shanghai")
    ai = FakeAI(valid_response())
    summarizer = Summarizer(ai=ai, max_context_chars=1000)

    result = summarizer.summarize(
        group_id=123,
        group_name="测试群",
        window_start=datetime(2026, 8, 24, 0, tzinfo=timezone),
        window_end=datetime(2026, 8, 24, 22, tzinfo=timezone),
        messages=[
            message(
                "m1",
                datetime(2026, 8, 24, 9, tzinfo=timezone),
                "https://tianquan.gtimg.cn/nudgeaction/item/10/expression.jpg",
            ),
            message(
                "m2",
                datetime(2026, 8, 24, 10, tzinfo=timezone),
                "保留这条普通讨论",
            ),
        ],
        timezone=timezone,
    )

    user_prompt = ai.calls[0][1]["content"]
    assert "保留这条普通讨论" in user_prompt
    assert "tianquan.gtimg.cn" not in user_prompt
    assert "消息数量: 1" in user_prompt
    assert "今日消息（1 条）" in user_prompt
    assert "原始 2 条，清洗丢弃 1 条，实际纳入 1 条" in user_prompt
    assert result.source_messages == 2
    assert result.included_messages == 1
    assert result.discarded_messages == 1
