from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qq_digest.archive import Archive
from qq_digest.catchup import CatchupService
from qq_digest.models import GroupConfig, NormalizedMessage


NOW = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)


def _report(archive: Archive, root: Path, group_id: int, report_date: str,
            payload: dict, created_at: str) -> int:
    path = root / f"{group_id}-{report_date}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_id = archive.record_report(
        group_id=group_id, report_date=report_date,
        markdown_path=root / f"{group_id}-{report_date}.md",
        json_path=path, candidate_ids=[],
    )
    archive.connection.execute(
        "UPDATE reports SET created_at=? WHERE report_id=?", (created_at, report_id)
    )
    archive.connection.commit()
    return report_id


def _payload(text: str, message_id: str, *, legacy: bool = False) -> dict:
    result = {"overview": "概览", "main_topics": [{
        "topic": "发布", "summary": text, "message_ids": [message_id],
    }], "conclusions": [], "resources": [], "tasks": [], "open_questions": []}
    if not legacy:
        result["evidence_version"] = 1
    return result


def test_catchup_combines_groups_filters_dates_and_validates_sources(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([
        GroupConfig(group_id=11, name="研发群"),
        GroupConfig(group_id=22, name="产品群"),
    ])
    local_time = datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive.ingest([NormalizedMessage(
        msg_id="source-1", group_id=11, sender_qq=1001,
        timestamp=local_time, collected_at=local_time, text="今天发布",
    )])
    first = _report(archive, tmp_path, 11, "2026-09-29",
                    _payload("今天发布", "source-1"), "2026-09-29T02:00:00+00:00")
    _report(archive, tmp_path, 22, "2026-09-28",
            _payload("讨论了设计", "source-1"), "2026-09-28T04:00:00+00:00")
    _report(archive, tmp_path, 22, "2026-09-19",
            _payload("很早的讨论", "missing"), "2026-09-19T04:00:00+00:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")

    today = service.list_items("today", now=NOW)
    assert today["total"] == 1
    assert today["items"][0]["group_name"] == "研发群"
    assert today["items"][0]["source_ids"] == ["source-1"]
    assert today["items"][0]["report_url"] == f"/reports?kind=daily&id={first}"

    week = service.list_items("week", now=NOW)
    assert [item["group_name"] for item in week["items"]] == ["研发群", "产品群"]
    assert week["items"][1]["status"] == "unverified"
    assert week["items"][1]["source_ids"] == []

    since = service.list_items("since", since="2026-09-28T12:00:00+00:00", now=NOW)
    assert since["total"] == 1
    assert since["items"][0]["group_name"] == "研发群"
    archive.close()


def test_catchup_visit_read_state_and_report_revision(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    _report(archive, tmp_path, 11, "2026-09-29",
            _payload("第一版", "missing"), "2026-09-29T02:00:00+00:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    assert service.visit(now=NOW)["previous_viewed_at"] is None
    assert service.visit(now=NOW.replace(hour=9))["previous_viewed_at"] == NOW.isoformat()

    item = service.list_items("today", now=NOW)["items"][0]
    assert item["read"] is False
    service.set_read(item["key"], True, now=NOW)
    assert service.list_items("today", now=NOW)["items"][0]["read"] is True
    archive.close()

    reopened = Archive.open(tmp_path / "archive.sqlite")
    service = CatchupService(reopened, timezone_name="Asia/Shanghai")
    assert service.list_items("today", now=NOW)["items"][0]["read"] is True
    _report(reopened, tmp_path, 11, "2026-09-29",
            _payload("第二版", "missing"), "2026-09-29T03:00:00+00:00")
    revised = service.list_items("today", now=NOW)["items"][0]
    assert revised["key"] != item["key"]
    assert revised["read"] is False
    service.set_read(revised["key"], True, now=NOW)
    service.set_read(revised["key"], False, now=NOW)
    assert service.list_items("today", now=NOW)["items"][0]["read"] is False
    with pytest.raises(ValueError, match="条目"):
        service.set_read("invalid", True, now=NOW)
    reopened.close()


def test_catchup_legacy_and_missing_report_are_explained(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    _report(archive, tmp_path, 11, "2026-09-29",
            _payload("旧版内容", "invented", legacy=True), "2026-09-29T02:00:00+00:00")
    _report(archive, tmp_path, 11, "2026-09-28",
            _payload("已丢失", "invented"), "2026-09-28T02:00:00+00:00")
    (tmp_path / "11-2026-09-28.json").unlink()
    result = CatchupService(archive, timezone_name="Asia/Shanghai").list_items("week", now=NOW)
    assert result["total"] == 1
    assert result["skipped_reports"] == 1
    assert result["items"][0]["status"] == "legacy"
    assert result["items"][0]["source_ids"] == []
    archive.close()


def test_catchup_paginates_without_losing_unread_count(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    payload = _payload("第一条", "missing")
    payload["main_topics"] = [
        {"topic": f"话题 {index}", "summary": "内容", "message_ids": []}
        for index in range(31)
    ]
    _report(archive, tmp_path, 11, "2026-09-29", payload, "2026-09-29T02:00:00+00:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    first = service.list_items("today", now=NOW)
    second = service.list_items("today", now=NOW, page=2)
    assert (first["total"], len(first["items"]), len(second["items"])) == (31, 30, 1)
    service.set_read(first["items"][0]["key"], True, now=NOW)
    assert service.list_items("today", now=NOW, page=2)["unread"] == 30
    archive.close()


def test_catchup_keeps_read_state_when_another_topic_is_inserted_before_it(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    payload = _payload("原有内容", "missing")
    _report(archive, tmp_path, 11, "2026-09-29", payload, "2026-09-29T02:00:00+00:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    original = service.list_items("today", now=NOW)["items"][0]
    service.set_read(original["key"], True, now=NOW)
    payload["main_topics"].insert(0, {
        "topic": "新话题", "summary": "新增内容", "message_ids": [],
    })
    _report(archive, tmp_path, 11, "2026-09-29", payload, "2026-09-29T03:00:00+00:00")
    unchanged = service.list_items("today", now=NOW)["items"][1]
    assert unchanged["key"] == original["key"]
    assert unchanged["read"] is True
    archive.close()


def test_custom_scope_filters_before_paging_and_counts_unread_and_new_independently(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群"),
                           GroupConfig(group_id=22, name="产品群")])
    for group_id, report_date, created_at in [
        (11, "2026-09-29", "2026-09-29T02:00:00+00:00"),
        (11, "2026-09-28", "2026-09-28T02:00:00+00:00"),
        (22, "2026-09-29", "2026-09-29T03:00:00+00:00"),
        (11, "2026-09-19", "2026-09-19T03:00:00+00:00"),
    ]:
        _report(archive, tmp_path, group_id, report_date,
                _payload(report_date, "missing"), created_at)
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    options = dict(date_from="2026-09-28", date_to="2026-09-29", group_id=11,
                   since="2026-09-29T09:00:00+08:00", now=NOW, page_size=1)
    all_items = service.list_items("custom", **options)
    newest = all_items["items"][0]
    service.set_read(newest["key"], True, now=NOW)
    unread = service.list_items("custom", read_filter="unread", **options)
    assert (unread["total"], unread["unread"], unread["new_count"]) == (1, 1, 1)
    assert unread["items"][0]["report_date"] == "2026-09-28"
    assert unread["items"][0]["new"] is False
    new = service.list_items("custom", read_filter="new", **options)
    assert new["items"][0]["key"] == newest["key"]
    assert new["items"][0]["read"] is True
    assert (new["total"], new["unread"], new["new_count"]) == (1, 1, 1)
    assert service.list_items("custom", read_filter="unread", page=2, **options)["items"] == []
    service.visit(now=NOW)
    assert service.list_items("custom", read_filter="unread", **options)["total"] == 1
    without_since = {key: value for key, value in options.items() if key != "since"}
    assert service.list_items("custom", read_filter="new", **without_since)["total"] == 2
    # The added selection parameters preserve the legacy feed's item keys.
    assert service.list_items("today", now=NOW)["items"][1]["key"] == newest["key"]
    archive.close()


@pytest.mark.parametrize("options", [
    {"date_from": "2026-09-30", "date_to": "2026-09-29"},
    {"date_from": "2026-9-28", "date_to": "2026-09-29"},
    {"date_from": "2026-09-28T00:00:00", "date_to": "2026-09-29"},
    {"date_from": "2026-02-30", "date_to": "2026-09-29"},
    {"since": "2026-09-29T01:00:00"},
    {"since": "not-a-date"},
    {"read_filter": "read"},
])
def test_custom_scope_rejects_invalid_selection(tmp_path, options):
    archive = Archive.open(tmp_path / "archive.sqlite")
    try:
        with pytest.raises(ValueError):
            CatchupService(archive, timezone_name="Asia/Shanghai").list_items(
                "custom", now=NOW, **options
            )
    finally:
        archive.close()


def test_custom_scope_uses_local_natural_day_and_strict_new_cutoff(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    _report(archive, tmp_path, 11, "2026-09-30", _payload("本地今日", "missing"),
            "2026-09-30T00:30:00+08:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    now = datetime(2026, 9, 29, 17, tzinfo=timezone.utc)
    result = service.list_items("custom", now=now, since="2026-09-29T16:30:00+00:00")
    assert result["total"] == 1
    assert result["new_count"] == 0
    assert result["items"][0]["new"] is False
    archive.close()


def test_legacy_since_empty_string_keeps_week_fallback(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    _report(archive, tmp_path, 11, "2026-09-28", _payload("本周", "missing"),
            "2026-09-28T02:00:00+00:00")
    _report(archive, tmp_path, 11, "2026-09-19", _payload("上周", "missing"),
            "2026-09-19T02:00:00+00:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    assert service.list_items("since", since="", now=NOW) == service.list_items("since", now=NOW)
    assert service.list_items("since", since="", now=NOW)["total"] == 1
    archive.close()


@pytest.mark.parametrize("scope", ["today", "week"])
@pytest.mark.parametrize("since", ["invalid", "2026-09-29T00:00:00", ""])
def test_legacy_date_scopes_ignore_invalid_since(tmp_path, scope, since):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群")])
    _report(archive, tmp_path, 11, "2026-09-29", _payload("原有内容", "missing"),
            "2026-09-29T02:00:00+00:00")
    service = CatchupService(archive, timezone_name="Asia/Shanghai")
    assert service.list_items(scope, since=since, now=NOW) == service.list_items(scope, now=NOW)
    assert service.list_items(scope, since="2026-09-29T02:00:00Z", now=NOW)["new_count"] == 0
    archive.close()
