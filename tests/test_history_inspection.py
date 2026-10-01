from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage


NOW = datetime(2026, 9, 30, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def message(msg_id, group_id=123, stamp=None):
    return NormalizedMessage(msg_id=msg_id, group_id=group_id, sender_qq=1,
        timestamp=stamp or NOW - timedelta(days=3), collected_at=NOW, text="测试消息")


class Source:
    def __init__(self, messages, failed=()):
        self.messages, self.failed, self.calls = messages, failed, []

    def collect(self, group_id, start, end):
        self.calls.append((group_id, start, end))
        if group_id in self.failed:
            yield message("partial", group_id)
            raise RuntimeError("源库中途读取失败")
        yield from [m for m in self.messages if m.group_id == group_id]


@pytest.fixture
def archive(tmp_path):
    ar = Archive.open(tmp_path / "archive.sqlite")
    ar.upsert_groups([GroupConfig(group_id=123, name="测试群"),
        GroupConfig(group_id=456, name="第二群"), GroupConfig(group_id=789, name="禁用群", enabled=False)])
    yield ar
    ar.close()


def service(archive):
    from qq_digest.history_inspection import HistoryInspectionService
    return HistoryInspectionService(archive, timezone_name="Asia/Shanghai")


def group_result(archive, group_id=123):
    return next(g for g in service(archive).snapshot()["groups"] if g["group_id"] == group_id)


def test_finds_historical_middle_gap_without_changing_archive_or_cursor(archive):
    stored = [message("first"), message("latest", stamp=NOW-timedelta(hours=1))]
    archive.ingest(stored)
    archive.mark_sync(group_id=123, last_timestamp=NOW)
    state = dict(archive.connection.execute("SELECT * FROM sync_state").fetchone())
    result = service(archive).run(Source([*stored, message("middle")]), now=NOW)
    assert result["status"] == "success"
    group = group_result(archive)
    assert group["missing_days"] == [{"date": "2026-09-27", "count": 1}]
    assert group["source_count"] == 2  # today is excluded
    assert archive.count_messages(123) == 2
    assert dict(archive.connection.execute("SELECT * FROM sync_state").fetchone()) == state


def test_duplicates_and_same_id_in_other_group_do_not_hide_gap(archive):
    archive.ingest([message("same", group_id=456)])
    source = Source([message("same"), message("same")])
    service(archive).run(source, now=NOW)
    group = group_result(archive)
    assert group["source_count"] == 1
    assert group["missing_count"] == 1
    assert {c[0] for c in source.calls} == {123, 456}


def test_complete_local_dates_and_exclusive_end_are_respected(archive):
    source = Source([message("start", stamp=datetime(2026,9,23,tzinfo=ZoneInfo("Asia/Shanghai"))),
        message("utc", stamp=datetime(2026,9,29,15,59,59,tzinfo=timezone.utc)),
        message("today", stamp=datetime(2026,9,29,16,tzinfo=timezone.utc)),
        message("before", stamp=datetime(2026,9,22,23,59,59,tzinfo=ZoneInfo("Asia/Shanghai")))])
    service(archive).run(source, now=NOW)
    assert group_result(archive)["missing_days"] == [
        {"date": "2026-09-23", "count": 1}, {"date": "2026-09-29", "count": 1}]
    assert len(source.calls) == 2


def test_partial_read_failure_preserves_prior_success_and_marks_unknown(archive):
    service(archive).run(Source([message("old-gap")]), now=NOW)
    previous = group_result(archive)
    result = service(archive).run(Source([], failed=(123,)), now=NOW)
    assert result["status"] == "partial_success"
    group = group_result(archive)
    assert group["status"] == "failed"
    assert group["missing_days"] == previous["missing_days"]
    assert group["last_success_at"] == previous["last_success_at"]
    assert "中途" in group["error"]
    assert archive.count_messages(123) == 0


def test_failed_refresh_does_not_erase_previous_gaps(archive):
    service(archive).run(Source([message("gap")]), now=NOW)
    source = Source([])
    def failed_refresh():
        raise RuntimeError("QQ 未运行")
    result = service(archive).run(source, now=NOW, prepare=failed_refresh)
    assert result["status"] == "failed"
    assert not source.calls
    assert group_result(archive)["missing_count"] == 1
    assert service(archive).snapshot()["latest_run"]["error"] == "QQ 未运行"
    assert group_result(archive)["is_latest"] is False


def test_recheck_after_repair_clears_gap(archive):
    source = Source([message("gap")])
    service(archive).run(source, now=NOW)
    archive.ingest([message("gap")])
    service(archive).run(source, now=NOW)
    assert group_result(archive)["missing_count"] == 0
    assert group_result(archive)["is_latest"] is True


def test_settings_and_results_survive_reopen(archive, tmp_path):
    from qq_digest.history_inspection import InspectionSettings
    service(archive).update_settings(InspectionSettings(enabled=False, lookback_days=14, interval_hours=48))
    service(archive).run(Source([message("gap")]), now=NOW)
    reopened = Archive.open(tmp_path / "archive.sqlite")
    try:
        assert service(reopened).settings() == {"enabled": False, "lookback_days": 14, "interval_hours": 48}
        assert group_result(reopened)["missing_count"] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("values", [{"lookback_days": 0}, {"lookback_days": 32},
    {"lookback_days": True}, {"interval_hours": 0}, {"interval_hours": 169},
    {"enabled": "false"}, {"interval_hours": "24"}])
def test_settings_reject_invalid_or_coerced_values(values):
    from qq_digest.history_inspection import InspectionSettings
    with pytest.raises(ValidationError):
        InspectionSettings(**values)


def test_persistent_interval_disabled_setting_and_interrupted_run(archive):
    from qq_digest.history_inspection import InspectionSettings
    assert service(archive).due(NOW) is True
    job = archive.start_job("history_inspection")
    assert service(archive).due(NOW) is True  # abandoned run can be retried after restart
    archive.finish_job(job, "failed", "failure")
    with archive.transaction():
        archive.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (NOW.isoformat(), job))
    assert service(archive).due(NOW + timedelta(hours=23)) is False
    assert service(archive).due(NOW + timedelta(hours=24)) is True
    service(archive).update_settings(InspectionSettings(enabled=False))
    assert service(archive).due(NOW + timedelta(days=3)) is False


def test_large_source_compares_ids_in_safe_batches(archive):
    messages = [message(f"m{i}") for i in range(1205)]
    archive.ingest(messages[:1000])
    service(archive).run(Source(messages), now=NOW)
    assert group_result(archive)["source_count"] == 1205
    assert group_result(archive)["missing_count"] == 205


def test_empty_source_is_not_a_claim_of_complete_history_and_delete_cascades(archive):
    service(archive).run(Source([]), now=NOW)
    assert group_result(archive)["source_count"] == 0
    assert "可读取" in group_result(archive)["label"]
    archive.delete_group(123)
    assert archive.connection.execute("SELECT 1 FROM history_inspection_groups WHERE group_id=123").fetchone() is None


def test_pagination_recovers_when_groups_are_disabled_between_reads(archive):
    assert service(archive).snapshot(page=2, page_size=1)["page"] == 2
    archive.upsert_groups([GroupConfig(group_id=456, name="第二群", enabled=False)])
    result = service(archive).snapshot(page=2, page_size=1)
    assert result["page"] == 1
    assert result["total"] == 1
    assert result["groups"][0]["group_id"] == 123


def test_scheduled_inspection_respects_due_time_and_conflicting_operation(archive, tmp_path, monkeypatch):
    import asyncio
    from qq_digest.config import AIConfig, Config, NTQQConfig, SecurityConfig
    from qq_digest.web.operations import OperationCoordinator
    from qq_digest.web.history_inspection import run_scheduled_inspection
    config = Config(data_dir=tmp_path, archive_path=tmp_path/"archive.sqlite", report_dir=tmp_path/"reports",
        knowledge_dir=tmp_path/"knowledge", work_dir=tmp_path/"work", log_dir=tmp_path/"logs",
        ai=AIConfig(base_url="https://example.com",model="test",api_key_env="TEST_KEY"),
        security=SecurityConfig(web_password_hash="x"*32), ntqq=NTQQConfig(enabled=True,db_dir="unused"))
    calls = []
    def inspect(**kwargs):
        calls.append(kwargs)
        service(archive).run(Source([]), now=NOW)
    monkeypatch.setattr("qq_digest.web.history_inspection.run_history_inspection", inspect)
    coordinator = OperationCoordinator()
    with coordinator.claim("sync"):
        assert asyncio.run(run_scheduled_inspection(config, coordinator)) is False
    assert not calls
    assert asyncio.run(run_scheduled_inspection(config, coordinator)) is True
    assert len(calls) == 1 and calls[0]["refresh"] is False
    assert asyncio.run(run_scheduled_inspection(config, coordinator)) is False
    assert len(calls) == 1
