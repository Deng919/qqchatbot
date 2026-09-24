from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.report_qa import (
    NoReportEvidence,
    ReportNotFound,
    answer_report_question,
    load_report_evidence,
    select_report_context,
)


TZ = ZoneInfo("Asia/Shanghai")


def message(msg_id: str, group_id: int, day: int, hour: int, minute: int, text: str) -> NormalizedMessage:
    timestamp = datetime(2026, 9, day, hour, minute, tzinfo=TZ)
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=group_id,
        sender_qq=1001,
        timestamp=timestamp,
        text=text,
        collected_at=timestamp,
    )


@pytest.fixture
def archive(tmp_path):
    result = Archive.open(tmp_path / "archive.sqlite")
    result.upsert_groups(
        [GroupConfig(group_id=1, name="甲群"), GroupConfig(group_id=2, name="乙群")]
    )
    yield result
    result.connection.close()


def test_daily_evidence_uses_only_its_group_and_local_day(archive, tmp_path):
    archive.ingest(
        [
            message("before", 1, 1, 0, 0, "前一天边界之外"),
            message("first", 1, 2, 0, 0, "当天开始"),
            message("last", 1, 2, 23, 59, "当天结束"),
            message("after", 1, 3, 0, 0, "次日不包含"),
            message("other-group", 2, 2, 12, 0, "其他群不包含"),
        ]
    )
    report_id = archive.record_report(
        group_id=1,
        report_date="2026-09-02",
        markdown_path=tmp_path / "daily.md",
        json_path=tmp_path / "daily.json",
        candidate_ids=[],
    )

    evidence = load_report_evidence(archive, "daily", report_id, TZ)

    assert [item.msg_id for item in evidence.messages] == ["first", "last"]
    assert evidence.group_id == 1
    assert evidence.start_date == "2026-09-02"
    assert evidence.end_date == "2026-09-02"


def test_range_evidence_includes_end_date_but_not_next_day(archive, tmp_path):
    archive.ingest(
        [
            message("start", 1, 1, 0, 0, "起始"),
            message("end", 1, 2, 23, 59, "结束日"),
            message("after", 1, 3, 0, 0, "次日"),
        ]
    )
    report_id = archive.record_manual_report(
        group_id=1,
        start_date="2026-09-01",
        end_date="2026-09-02",
        detail_mode="group",
        effective_template="adaptive",
        markdown_path=tmp_path / "range.md",
        json_path=tmp_path / "range.json",
        candidate_ids=[],
        input_fingerprint="range",
        source_message_count=3,
    )

    evidence = load_report_evidence(archive, "range", report_id, TZ)

    assert [item.msg_id for item in evidence.messages] == ["start", "end"]


def test_missing_report_and_empty_archive_have_distinct_errors(archive, tmp_path):
    with pytest.raises(ReportNotFound):
        load_report_evidence(archive, "daily", 999, TZ)
    report_id = archive.record_report(
        group_id=1,
        report_date="2026-09-02",
        markdown_path=tmp_path / "daily.md",
        json_path=tmp_path / "daily.json",
        candidate_ids=[],
    )
    with pytest.raises(NoReportEvidence):
        load_report_evidence(archive, "daily", report_id, TZ)


def test_context_uses_entire_window_when_within_budget():
    messages = [
        message("a", 1, 2, 10, 0, "接口报错 503"),
        message("b", 1, 2, 10, 1, "稍后恢复"),
    ]

    selected = select_report_context(messages, "为什么报错 503？", max_chars=1000)

    assert [item.msg_id for item in selected.messages] == ["a", "b"]
    assert selected.truncated is False
    assert selected.message_count == 2
    assert len(selected.text) <= 1000


def test_context_prefers_matching_messages_and_neighbors_when_too_long():
    messages = [
        message("a", 1, 2, 10, 0, "其他话题" * 20),
        message("b", 1, 2, 10, 1, "服务器开始不稳定"),
        message("c", 1, 2, 10, 2, "接口报错 503"),
        message("d", 1, 2, 10, 3, "后来已恢复"),
        message("e", 1, 2, 10, 4, "另一个无关话题" * 20),
    ]

    selected = select_report_context(messages, "503 错误为什么发生？", max_chars=230)

    assert "c" in [item.msg_id for item in selected.messages]
    assert "b" in [item.msg_id for item in selected.messages]
    assert "d" in [item.msg_id for item in selected.messages]
    assert "a" not in [item.msg_id for item in selected.messages]
    assert "e" not in [item.msg_id for item in selected.messages]
    assert selected.truncated is True
    assert len(selected.text) <= 230


def test_answer_uses_report_and_only_checked_source_ids(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("接口异常摘要", encoding="utf-8")
    messages = [message("m1", 1, 2, 10, 0, "接口报错 503")]
    evidence = type("Evidence", (), {
        "group_id": 1, "start_date": "2026-09-02", "end_date": "2026-09-02",
        "markdown_path": report, "messages": messages,
    })()

    class FakeAI:
        def chat(self, prompts):
            assert "接口异常摘要" in str(prompts)
            assert "m1" in str(prompts)
            return {"answer": "出现了 503，但群聊没有说明根因。", "source_ids": ["m1", "invented"]}

    result = answer_report_question(evidence, "为什么出错？", [], FakeAI(), max_chars=1000)

    assert result["answer"] == "出现了 503，但群聊没有说明根因。"
    assert [item["msg_id"] for item in result["sources"]] == ["m1"]
    assert result["context_message_count"] == 1


def test_answer_without_valid_source_states_uncertainty(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("摘要", encoding="utf-8")
    evidence = type("Evidence", (), {
        "group_id": 1, "start_date": "2026-09-02", "end_date": "2026-09-02",
        "markdown_path": report, "messages": [message("m1", 1, 2, 10, 0, "没提到原因")],
    })()

    class FakeAI:
        def chat(self, prompts):
            return {"answer": "肯定是网络原因", "source_ids": ["fabricated"]}

    result = answer_report_question(evidence, "原因？", [], FakeAI())

    assert result["sources"] == []
    assert "无法" in result["answer"] or "不能" in result["answer"]
