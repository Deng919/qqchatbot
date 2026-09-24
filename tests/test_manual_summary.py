import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from qq_digest.archive import Archive
from qq_digest.manual_summary import (
    ManualSummaryRequest,
    ManualSummaryService,
    range_window,
)
from qq_digest.models import GroupConfig, NormalizedMessage


class RecordingAI:
    def __init__(self, failing_groups=()):
        self.failing_groups = set(failing_groups)
        self.calls: list[int] = []
        self.prompts: list[str] = []

    def chat(self, messages):
        prompt = messages[-1]["content"]
        group_id = int(re.search(r"群ID: (\d+)", prompt).group(1))
        self.calls.append(group_id)
        self.prompts.append(prompt)
        if group_id in self.failing_groups:
            raise RuntimeError("temporary AI outage")
        message_id = {123: "m1", 456: "m2"}.get(group_id, "m3")
        return {
            "group_id": group_id,
            "main_topics": [],
            "conclusions": [],
            "resources": [],
            "tasks": [],
            "open_questions": [],
            "candidates": [
                {
                    "type": "resource",
                    "title": f"资源 {group_id}",
                    "link": "https://example.com",
                    "content": "说明",
                    "reason": "长期有用",
                    "message_ids": [message_id],
                }
            ],
        }


def add_message(archive: Archive, group_id: int, msg_id: str, hour: int = 10):
    timestamp = datetime(2026, 9, 2, hour, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive.ingest(
        [
            NormalizedMessage(
                msg_id=msg_id,
                group_id=group_id,
                sender_qq=1,
                timestamp=timestamp,
                text=f"群 {group_id} 的归档消息",
                collected_at=timestamp,
            )
        ]
    )


def make_service(tmp_path, groups, ai=None):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups(groups)
    client = ai or RecordingAI()
    service = ManualSummaryService(
        archive=archive,
        ai_client=client,
        report_dir=tmp_path / "reports",
        max_context_chars=1000,
        timezone_name="Asia/Shanghai",
        knowledge_paths={},
    )
    return service, archive, client


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"group_ids": ()}, "至少选择一个群"),
        ({"group_ids": (123, 123)}, "重复"),
        (
            {"start_date": date(2026, 9, 2), "end_date": date(2026, 9, 1)},
            "不得晚于",
        ),
        (
            {"start_date": date(2026, 9, 1), "end_date": date(2026, 9, 8)},
            "最多选择 7 个自然日",
        ),
    ],
)
def test_manual_summary_request_validation(kwargs, match):
    values = {
        "group_ids": (123,),
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 7),
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=match):
        ManualSummaryRequest(**values)


def test_range_window_includes_end_date_and_excludes_next_midnight():
    request = ManualSummaryRequest(
        group_ids=(123,),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
    )

    start, end = range_window(request, ZoneInfo("Asia/Shanghai"))

    assert start.isoformat() == "2026-09-01T00:00:00+08:00"
    assert end.isoformat() == "2026-09-08T00:00:00+08:00"


def test_manual_summary_generates_per_group_and_reuses_same_input(tmp_path):
    service, archive, ai = make_service(
        tmp_path,
        [
            GroupConfig(group_id=123, name="群一"),
            GroupConfig(group_id=456, name="群二"),
        ],
    )
    add_message(archive, 123, "m1")
    add_message(archive, 456, "m2")
    request = ManualSummaryRequest(
        group_ids=(123, 456),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
    )

    first = service.run(request)
    second = service.run(request)

    assert first.status == "success"
    assert [item.group_id for item in first.created_reports] == [123, 456]
    assert second.created_reports == []
    assert [item.group_id for item in second.reused_reports] == [123, 456]
    assert ai.calls == [123, 456]
    assert archive.connection.execute(
        "SELECT COUNT(*) FROM manual_reports"
    ).fetchone()[0] == 2
    assert archive.connection.execute(
        "SELECT COUNT(*) FROM candidates"
    ).fetchone()[0] == 2
    assert archive.connection.execute(
        "SELECT COUNT(*) FROM send_log"
    ).fetchone()[0] == 0
    assert all("模板:" not in prompt for prompt in ai.prompts)
    assert {
        row["effective_template"]
        for row in archive.connection.execute("SELECT * FROM manual_reports")
    } == {"adaptive"}


def test_manual_summary_ignores_legacy_group_template(tmp_path):
    service, archive, ai = make_service(
        tmp_path, [GroupConfig(group_id=123, name="群一", template="detailed")]
    )
    add_message(archive, 123, "m1")

    result = service.run(
        ManualSummaryRequest(
            group_ids=(123,),
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
        )
    )

    assert result.created_reports[0].group_id == 123
    assert "模板:" not in ai.prompts[0]
    row = archive.manual_report_by_id(result.created_reports[0].report_id)
    assert row["effective_template"] == "adaptive"


def test_manual_summary_skips_empty_group_and_isolates_failure(tmp_path):
    service, archive, _ = make_service(
        tmp_path,
        [
            GroupConfig(group_id=123, name="成功群"),
            GroupConfig(group_id=456, name="失败群"),
            GroupConfig(group_id=789, name="空群"),
        ],
        ai=RecordingAI(failing_groups={456}),
    )
    add_message(archive, 123, "m1")
    add_message(archive, 456, "m2")

    result = service.run(
        ManualSummaryRequest(
            group_ids=(123, 456, 789),
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
        )
    )

    assert result.status == "partial_success"
    assert [item.group_id for item in result.created_reports] == [123]
    assert result.skipped_groups == [
        {"group_id": 789, "group_name": "空群", "reason": "所选范围没有归档消息"}
    ]
    assert result.failed_groups[0].group_id == 456
    assert result.failed_groups[0].stage == "summary"
    job = archive.connection.execute(
        "SELECT status, error FROM jobs WHERE job_type='manual_summary'"
    ).fetchone()
    assert job["status"] == "partial_success"
    assert "temporary AI outage" in job["error"]


def test_manual_summary_rejects_missing_or_disabled_groups(tmp_path):
    service, _, _ = make_service(
        tmp_path, [GroupConfig(group_id=123, name="停用群", enabled=False)]
    )

    with pytest.raises(ValueError, match="不存在或未启用"):
        service.run(
            ManualSummaryRequest(
                group_ids=(123, 999),
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 1),
            )
        )


def test_manual_summary_regenerates_when_archived_messages_change(tmp_path):
    service, archive, ai = make_service(
        tmp_path, [GroupConfig(group_id=123, name="群一")]
    )
    add_message(archive, 123, "m1")
    request = ManualSummaryRequest(
        group_ids=(123,),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
    )
    first = service.run(request)
    first_report_id = first.created_reports[0].report_id
    first_candidate_ids = archive.candidates_for_report(
        archive.manual_report_by_id(first_report_id)["candidate_ids"]
    )
    add_message(archive, 123, "m-new", hour=11)

    second = service.run(request)
    row = archive.manual_report_by_id(first_report_id)

    assert second.created_reports[0].report_id == first_report_id
    assert row["source_message_count"] == 2
    assert ai.calls == [123, 123]
    assert archive.connection.execute(
        "SELECT COUNT(*) FROM candidates"
    ).fetchone()[0] == 1
    assert archive.candidates_for_report(row["candidate_ids"]) == first_candidate_ids
    assert archive.connection.execute(
        "SELECT 1 FROM candidates WHERE candidate_id=?", (first_candidate_ids[0],)
    ).fetchone()


def test_manual_summary_publication_failure_preserves_previous_state(
    tmp_path, monkeypatch
):
    service, archive, _ = make_service(
        tmp_path, [GroupConfig(group_id=123, name="群一")]
    )
    add_message(archive, 123, "m1")
    request = ManualSummaryRequest(
        group_ids=(123,),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
    )
    first = service.run(request)
    report_id = first.created_reports[0].report_id
    old_row = archive.manual_report_by_id(report_id)
    old_markdown = (tmp_path / "reports" / "range__2026-09-01__2026-09-07__123__adaptive.md").read_text(encoding="utf-8")
    old_candidate_ids = old_row["candidate_ids"]
    add_message(archive, 123, "m-new", hour=11)

    def fail_record(**kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(
        archive, "record_manual_report_in_transaction", fail_record
    )
    result = service.run(request)
    current = archive.manual_report_by_id(report_id)

    assert result.status == "partial_success"
    assert result.failed_groups[0].stage == "publication"
    assert current["input_fingerprint"] == old_row["input_fingerprint"]
    assert current["candidate_ids"] == old_candidate_ids
    assert (tmp_path / "reports" / "range__2026-09-01__2026-09-07__123__adaptive.md").read_text(encoding="utf-8") == old_markdown
    assert not list((tmp_path / "reports").glob(".*.tmp"))
    assert not list((tmp_path / "reports").glob(".*.bak"))
