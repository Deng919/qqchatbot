import json
from datetime import datetime, timezone

from qq_digest.archive import Archive
from qq_digest.failure_center import FailureCenterService
from qq_digest.models import GroupConfig


def test_failure_center_shows_group_stage_and_retry_outcome(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="开发群")])
    job_id = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job_id, "partial_success", json.dumps([{
        "group_id": 123, "group_name": "开发群", "stage": "summary",
        "error": "AI 请求超时",
    }], ensure_ascii=False))
    archive.mark_sync_failure(group_id=123, status="adapter_incompatible", error="消息库不可读")

    result = FailureCenterService(archive).list_items()
    summary = next(item for item in result["items"] if item["kind"] == "daily_digest")
    sync = next(item for item in result["items"] if item["kind"] == "group_sync")
    assert summary["job_id"] == job_id
    assert summary["group_name"] == "开发群"
    assert summary["stage"] == "summary"
    assert summary["error"] == "AI 请求超时"
    assert summary["status"] == "active"
    assert sync["group_id"] == 123
    assert sync["action"] == "collect"

    retry_id = archive.start_job(
        "daily_digest", target_date="2026-09-29", retry_of_job_id=job_id
    )
    archive.finish_job(retry_id, "success", group_outcomes={123: "success"})
    summary = next(item for item in FailureCenterService(archive).list_items()["items"]
                   if item["id"] == f"job:{job_id}:123")
    assert summary["status"] == "recovered"
    assert summary["retry_job_id"] == retry_id
    assert summary["retry_status"] == "success"


def test_failure_center_does_not_mark_disabled_group_recovered(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="停用群")])
    original = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(original, "partial_success", json.dumps([{
        "group_id": 123, "group_name": "停用群", "stage": "summary",
        "error": "AI 超时",
    }], ensure_ascii=False))
    later = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(later, "success", group_outcomes={})
    original_item = next(item for item in FailureCenterService(archive).list_items()["items"]
                         if item["job_id"] == original)
    assert original_item["status"] == "active"


def test_failure_center_group_recovers_even_if_other_group_still_fails(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    original = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(original, "partial_success", json.dumps([{
        "group_id": 123, "group_name": "甲群", "stage": "summary", "error": "超时",
    }], ensure_ascii=False))
    later = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(later, "partial_success", "其他群失败", group_outcomes={123: "success", 456: "failed"})
    item = next(item for item in FailureCenterService(archive).list_items()["items"]
                if item["job_id"] == original)
    assert item["status"] == "recovered"


def test_failure_center_keeps_old_failed_notification_after_recovered_history(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    old = archive.enqueue_notification(
        report_id=None, channel="qq_bot_private", recipient="old", payload={}
    )
    archive.mark_notification_retry(
        old, error="网络错误", next_attempt_at=datetime.now(timezone.utc), max_attempts=1,
    )
    for index in range(105):
        send_id = archive.enqueue_notification(
            report_id=None, channel="qq_bot_private", recipient=f"recipient-{index}", payload={}
        )
        archive.mark_notification_retry(
            send_id, error="限流", next_attempt_at=datetime.now(timezone.utc), max_attempts=2,
        )
        archive.mark_notification_success(send_id)
    assert any(item.get("send_id") == old for item in FailureCenterService(archive).list_items()["items"])


def test_failure_center_notification_tracks_manual_retry(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    send_id = archive.enqueue_notification(
        report_id=None, channel="qq_bot_private", recipient="private-openid",
        payload={"group_name": "开发群", "report_date": "2026-09-29"},
    )
    archive.mark_notification_retry(
        send_id, error="网络错误", next_attempt_at=datetime.now(timezone.utc),
        max_attempts=1,
    )
    item = FailureCenterService(archive).list_items()["items"][0]
    assert item["kind"] == "notification"
    assert item["group_name"] == "开发群"
    assert item["error"] == "网络错误"
    assert "private-openid" not in str(item)
    assert item["status"] == "active"
    archive.retry_failed_notification(send_id)
    item = FailureCenterService(archive).list_items()["items"][0]
    assert item["status"] == "pending"
    archive.mark_notification_success(send_id)
    item = FailureCenterService(archive).list_items()["items"][0]
    assert item["status"] == "recovered"


def test_failure_center_preserves_malformed_legacy_job_error(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    job_id = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job_id, "failed", "旧格式错误")
    item = FailureCenterService(archive).list_items()["items"][0]
    assert item["job_id"] == job_id
    assert item["error"] == "旧格式错误"
    assert item["group_name"] == "全部已启用群"


def test_failure_center_keeps_old_unresolved_failure_after_many_successes(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    old = archive.start_job("daily_digest", target_date="2026-08-01")
    archive.finish_job(old, "failed", "AI 服务不可用")
    for index in range(105):
        job = archive.start_job("message_sync")
        archive.finish_job(job, "success")
    items = FailureCenterService(archive).list_items()["items"]
    assert any(item["job_id"] == old for item in items)
