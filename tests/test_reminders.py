from datetime import datetime, timedelta, timezone
import json

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig
from qq_digest.reminders import ReminderConflict, ReminderService
from tests.factories import make_message


NOW = datetime(2026, 10, 5, 4, tzinfo=timezone.utc)


@pytest.fixture
def service(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群"), GroupConfig(group_id=456, name="另一个群")])
    yield ReminderService(archive)
    archive.connection.close()


def rule(kind="keyword", **kwargs):
    return dict(name="规则", kind=kind, enabled=True, group_ids=[],
                channels=["in_app", "windows"], keywords=["hello"],
                quiet_start="00:00", quiet_end="00:00", **kwargs)


def message(service, msg_id, text="HELLO", group_id=123):
    service.archive.ingest([make_message(msg_id, NOW).model_copy(update=dict(text=text, group_id=group_id))])


def candidate(service, link="https://example.com", status="pending", kind="resource"):
    c = service.archive.connection
    cur = c.execute("""INSERT INTO candidates(group_id,message_ids,created_date,candidate_type,title,
        link,content,reason,excerpt,status,created_at,updated_at)
        VALUES(123,'[]','2026-10-05',?,'资源',?,'内容','','',?,?,?)""",
        (kind, link, status, NOW.isoformat(), NOW.isoformat()))
    c.commit()
    return cur.lastrowid


def task(service, due="2026-10-05"):
    message(service, "tasksource")
    c = service.archive.connection
    cur = c.execute("""INSERT INTO tasks(group_id,title,due_date,source_ids,primary_source_id,created_at,updated_at)
        VALUES(123,'完成任务',?,'["tasksource"]','tasksource',?,?)""", (due, NOW.isoformat(), NOW.isoformat()))
    c.commit()
    return cur.lastrowid


def fault(service, at=NOW, error="读取失败", group_id=123, retry=None, status="failed"):
    c = service.archive.connection
    data = json.dumps([dict(group_id=group_id, stage="collection", error=error)]) if group_id else error
    cur = c.execute("""INSERT INTO jobs(job_type,status,started_at,finished_at,error,target_date,retry_of_job_id)
        VALUES('message_sync',?,?,?,?, '2026-10-05',?)""", (status, at.isoformat(), at.isoformat(), data, retry))
    if group_id:
        c.execute("INSERT INTO job_group_results(job_id,group_id,status) VALUES(?,?,?)", (cur.lastrowid, group_id, "success" if status == "success" else "failed"))
    c.commit()
    return cur.lastrowid


def test_message_baseline_normalization_scope_and_restart(service):
    message(service, "old")
    values = rule()
    values["group_ids"] = [123]
    r = service.save_rule(values, now=NOW)
    message(service, "new", "ｈｅｌｌｏ")
    message(service, "wrong-group", group_id=456)
    assert service.scan(now=NOW)["created"] == 1
    assert ReminderService(service.archive).scan(now=NOW)["created"] == 0
    e = service.list_events()["items"][0]
    assert e["rule_id"] == r["rule_id"] and e["group_name"] == "测试群"


def test_message_cursor_processes_actual_200_rows(service):
    service.save_rule(rule(), now=NOW)
    for index in range(205):
        message(service, str(index))
    assert service.scan(now=NOW)["created"] == 200
    assert service.scan(now=NOW)["created"] == 5
    assert service.scan(now=NOW)["created"] == 0


def test_resource_baseline_link_dedupe_and_ignored(service):
    candidate(service, "old")
    service.save_rule(rule("resource"), now=NOW)
    candidate(service)
    candidate(service)
    candidate(service, "ignored", "ignored")
    candidate(service, "task", kind="task")
    candidate(service, "")
    candidate(service, "")
    assert service.scan(now=NOW)["created"] == 3


def test_tasks_timezone_current_due_and_changed_queue(service):
    ident = task(service)
    values = rule("task_due")
    values.update(quiet_start="00:00", quiet_end="23:59")
    service.save_rule(values, now=NOW)
    assert service.scan(now=NOW)["created"] == 1
    assert service.list_events()["total"] == 0
    service.archive.connection.execute("UPDATE tasks SET due_date='2026-10-06' WHERE task_id=?", (ident,))
    service.archive.connection.commit()
    assert service.scan(now=NOW + timedelta(days=1))["created"] == 1
    service.archive.connection.execute("UPDATE tasks SET status='completed' WHERE task_id=?", (ident,))
    service.archive.connection.commit()
    calls = []
    service.dispatch_windows(lambda *args: calls.append(args) or True, now=NOW + timedelta(days=2))
    assert calls == []
    assert service.list_events()["total"] == 0


def test_failure_baseline_retry_aggregation_scope_and_recovery(service):
    fault(service, NOW - timedelta(hours=1))
    values = rule("failure")
    values["group_ids"] = [123]
    service.save_rule(values, now=NOW)
    first = fault(service, NOW + timedelta(seconds=1), error="新故障")
    fault(service, NOW + timedelta(seconds=2), error="新故障", retry=first)
    fault(service, NOW + timedelta(seconds=3), error="全局故障", group_id=None)
    assert service.scan(now=NOW + timedelta(seconds=4))["created"] == 1
    fault(service, NOW + timedelta(seconds=5), status="success")
    calls = []
    service.dispatch_windows(lambda *a: calls.append(a) or True, now=NOW + timedelta(seconds=6))
    assert not calls
    assert "新故障" not in service.list_events()["items"][0]["body"]


def test_quiet_cross_midnight_releases_and_windows_aggregate(service):
    values = rule()
    values.update(quiet_start="22:00", quiet_end="08:00")
    night = NOW.replace(hour=15)
    service.save_rule(values, now=night)
    message(service, "one")
    message(service, "two")
    assert service.scan(now=night)["queued"] == 2
    assert service.list_events()["total"] == 0
    calls = []
    assert service.dispatch_windows(lambda *a: calls.append(a) or True, now=night)["sent"] == 0
    service.scan(now=night + timedelta(hours=10))
    assert service.list_events()["total"] == 2
    assert service.dispatch_windows(lambda *a: calls.append(a) or True, now=night + timedelta(hours=10))["sent"] == 2
    assert len(calls) == 1 and "2" in calls[0][1]


def test_windows_batch_retry_bound_and_unknown(service):
    service.save_rule(rule(), now=NOW)
    for i in range(25):
        message(service, str(i))
    service.scan(now=NOW)
    calls = []
    sink = lambda *a: calls.append(a) or False
    assert service.dispatch_windows(sink, now=NOW)["failed"] == 20
    assert len(calls) == 1
    # Five untouched events remain eligible; failed events wait for backoff.
    assert service.dispatch_windows(sink, now=NOW)["failed"] == 5
    service.dispatch_windows(sink, now=NOW + timedelta(hours=1))
    service.dispatch_windows(sink, now=NOW + timedelta(hours=2))
    service.dispatch_windows(sink, now=NOW + timedelta(hours=3))
    service.dispatch_windows(sink, now=NOW + timedelta(hours=4))
    assert service.dispatch_windows(sink, now=NOW + timedelta(days=1))["failed"] == 0
    c = service.archive.connection
    c.execute("UPDATE reminder_events SET windows_status='sending' WHERE event_id=1")
    c.commit()
    restarted = ReminderService(service.archive)
    restarted.dispatch_windows(lambda *a: True, now=NOW + timedelta(days=2))
    assert restarted.list_events(page_size=100)["items"][-1]["delivery_status"]["windows"] == "unknown"


def test_rule_revision_condition_edit_disable_and_delete(service):
    r = service.save_rule(rule(), now=NOW)
    message(service, "between")
    edited = dict(rule(), name="修改")
    r2 = service.save_rule(edited, r["rule_id"], r["revision"], now=NOW)
    assert r2["revision"] == r["revision"] + 1
    assert service.scan(now=NOW)["created"] == 1
    with pytest.raises(ReminderConflict):
        service.save_rule(edited, r["rule_id"], r["revision"], now=NOW)
    message(service, "new")
    service.scan(now=NOW)
    service.delete_rule(r2["rule_id"], r2["revision"])
    assert service.list_rules()["rules"] == []
    assert service.list_events()["queued"] == 0
    assert service.dispatch_windows(lambda *a: pytest.fail("deleted rule sent"), now=NOW)["sent"] == 0
    with pytest.raises(LookupError):
        service.save_rule(edited, r["rule_id"], r2["revision"], now=NOW)


@pytest.mark.parametrize("change", [dict(enabled=1), dict(group_ids=[True]), dict(group_ids=[999]),
    dict(channels=["qq"]), dict(keywords=[]), dict(quiet_start="25:00"), dict(kind="other"), dict(name=" ")])
def test_validation(service, change):
    values = rule()
    values.update(change)
    with pytest.raises(ValueError):
        service.save_rule(values, now=NOW)


def test_preview_is_pure_historical_and_pagination_read(service):
    message(service, "old")
    before = service.archive.connection.total_changes
    preview = service.preview(rule(), now=NOW)
    assert preview["total"] == 1 and preview["items"][0]["group_id"] == 123
    assert service.archive.connection.total_changes == before
    service.save_rule(rule(), now=NOW)
    for i in range(3):
        message(service, str(i))
    service.scan(now=NOW)
    page = service.list_events(page=2, page_size=2)
    assert len(page["items"]) == 1 and page["total"] == 3 and page["unread"] == 3
    service.mark_read(page["items"][0]["event_id"], True)
    assert service.list_events(unread_only=True)["total"] == 2
    with pytest.raises(LookupError):
        service.mark_read(999, True)
    with pytest.raises(ValueError):
        service.list_events(page=0)


def test_scan_failure_rolls_back_events_and_watermark(service):
    service.save_rule(rule(), now=NOW)
    message(service, "one")
    c = service.archive.connection
    c.execute("CREATE TRIGGER reject_event BEFORE INSERT ON reminder_events BEGIN SELECT RAISE(ABORT,'test failure'); END")
    c.commit()
    with pytest.raises(Exception, match="test failure"):
        service.scan(now=NOW)
    c.execute("DROP TRIGGER reject_event")
    c.commit()
    assert service.scan(now=NOW)["created"] == 1


def test_group_delete_cleans_events_and_narrows_rules(service):
    values = rule()
    values["group_ids"] = [123]
    service.save_rule(values, now=NOW)
    message(service, "one")
    service.scan(now=NOW)
    result = service.archive.delete_group(123)
    assert result.deleted["reminder_events"] == 1
    r = service.list_rules()["rules"][0]
    assert r["enabled"] is False
    assert service.list_events()["total"] == 0


def test_existing_failure_retry_does_not_escape_save_baseline(service):
    old = fault(service, NOW - timedelta(minutes=1))
    service.save_rule(rule("failure"), now=NOW)
    fault(service, NOW + timedelta(minutes=1), retry=old)
    assert service.scan(now=NOW + timedelta(minutes=2))["created"] == 0


def test_windows_only_records_retry_and_disabled_rules(service):
    values = rule()
    values["channels"] = ["windows"]
    saved = service.save_rule(values, now=NOW)
    message(service, "one")
    service.scan(now=NOW)
    assert service.list_events()["total"] == 0
    assert service.dispatch_windows(None, now=NOW)["queued"] == 1
    service.dispatch_windows(lambda *a: False, now=NOW)
    event = service.list_events()["items"][0]
    assert event["delivery_status"]["windows"] == "failed"
    service.retry_windows(event["event_id"], now=NOW)
    assert service.dispatch_windows(lambda *a: True, now=NOW)["sent"] == 1
    with pytest.raises(ValueError):
        service.retry_windows(event["event_id"], now=NOW)
    message(service, "two")
    service.scan(now=NOW)
    values["enabled"] = False
    service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW)
    assert service.dispatch_windows(lambda *a: pytest.fail("disabled sent"), now=NOW)["sent"] == 0


def test_configured_timezone_due_date_and_reopen_dedupe(service):
    task_id = task(service)
    before_day = datetime(2026, 10, 4, 16, tzinfo=timezone.utc)
    service.save_rule(rule("task_due"), now=before_day)
    assert ReminderService(service.archive, "UTC").scan(now=before_day)["created"] == 0
    assert service.scan(now=before_day)["created"] == 1
    c = service.archive.connection
    c.execute("UPDATE tasks SET status='canceled' WHERE task_id=?", (task_id,))
    c.commit()
    service.scan(now=before_day)
    c.execute("UPDATE tasks SET status='open' WHERE task_id=?", (task_id,))
    c.commit()
    assert service.scan(now=before_day)["created"] == 0


def test_message_rowid_never_reused_after_group_deletion(service):
    service.save_rule(rule(), now=NOW)
    message(service, "one")
    assert service.scan(now=NOW)["created"] == 1
    old_rowid = service.archive.connection.execute("SELECT rowid FROM messages").fetchone()[0]
    service.archive.delete_group(123)
    service.archive.upsert_groups([GroupConfig(group_id=123, name="重建群")])
    message(service, "one")
    new_rowid = service.archive.connection.execute("SELECT rowid FROM messages").fetchone()[0]
    assert new_rowid > old_rowid
    assert service.scan(now=NOW)["created"] == 1


def test_sqlite_backup_preserves_rules_events_read_and_cursors(service, tmp_path):
    service.save_rule(rule(), now=NOW)
    message(service, "one")
    service.scan(now=NOW)
    event = service.list_events()["items"][0]
    service.mark_read(event["event_id"])
    destination = Archive.open(tmp_path / "snapshot.sqlite")
    service.archive.connection.backup(destination.connection)
    restored = ReminderService(destination)
    assert restored.list_rules() == service.list_rules()
    assert restored.list_events()["items"] == service.list_events()["items"]
    assert restored.scan(now=NOW)["created"] == 0
    destination.connection.close()


def test_deleted_group_failures_do_not_block_remaining_rules(service):
    service.save_rule(rule("failure"), now=NOW)
    fault(service, NOW + timedelta(minutes=1))
    service.archive.delete_group(123)
    assert service.scan(now=NOW + timedelta(minutes=2))["created"] == 0


def test_ingest_sequence_rolls_back_with_messages(service):
    c = service.archive.connection
    before = c.execute("SELECT high_water FROM message_ingest_sequence").fetchone()[0]
    c.execute("CREATE TRIGGER reject_message BEFORE INSERT ON messages WHEN new.msg_id='bad' BEGIN SELECT RAISE(ABORT,'ingest failure'); END")
    c.commit()
    with pytest.raises(Exception, match="ingest failure"):
        service.archive.ingest([make_message("good", NOW), make_message("bad", NOW)])
    assert c.execute("SELECT high_water FROM message_ingest_sequence").fetchone()[0] == before
    assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_page_offset_out_of_sqlite_range_is_validation_error(service):
    with pytest.raises(ValueError):
        service.list_events(page=10**100)


def test_condition_edits_cancel_queued_old_scope_events(service):
    values = rule()
    values.update(quiet_start="00:00", quiet_end="23:59")
    saved = service.save_rule(values, now=NOW)
    message(service, "one")
    service.scan(now=NOW)
    values["group_ids"] = [456]
    service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW)
    service.scan(now=NOW.replace(hour=15, minute=59))
    calls = []
    service.dispatch_windows(lambda *a: calls.append(a) or True, now=NOW.replace(hour=15, minute=59))
    assert not calls
    assert service.list_events()["total"] == 0


def test_removed_windows_channel_drops_queue_and_unknown_retry_checks_scope(service):
    values = rule()
    saved = service.save_rule(values, now=NOW)
    message(service, "one")
    service.scan(now=NOW)
    event_id = service.list_events()["items"][0]["event_id"]
    c = service.archive.connection
    c.execute("UPDATE reminder_events SET windows_status='unknown' WHERE event_id=?", (event_id,))
    c.commit()
    values["group_ids"] = [456]
    saved = service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW)
    with pytest.raises(ValueError):
        service.retry_windows(event_id, now=NOW)
    values["group_ids"] = []
    saved = service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW)
    message(service, "two")
    service.scan(now=NOW)
    values["channels"] = ["in_app"]
    service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW)
    assert service.list_events()["queued"] == 0


def test_historical_resource_links_are_suppressed_with_normalized_host(service):
    candidate(service, "https://EXAMPLE.com/docs")
    service.save_rule(rule("resource"), now=NOW)
    candidate(service, " https://example.com/docs ")
    candidate(service, "https://example.com/new")
    assert service.scan(now=NOW)["created"] == 1


def test_keyword_edit_cancels_queued_matching_prior_text(service):
    values = rule()
    values.update(quiet_start="00:00", quiet_end="23:59")
    saved = service.save_rule(values, now=NOW)
    message(service, "one")
    service.scan(now=NOW)
    values["keywords"] = ["other"]
    service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW)
    assert service.list_events()["queued"] == 0
    assert service.dispatch_windows(lambda *a: pytest.fail("prior keyword delivered"), now=NOW.replace(hour=15, minute=59))["sent"] == 0


def test_queued_failure_remains_active_beyond_failure_page_limit(service):
    from qq_digest.failure_center import FailureCenterService

    values = rule("failure")
    values.update(quiet_start="00:00", quiet_end="23:59")
    service.save_rule(values, now=NOW)
    original_job = fault(service, NOW + timedelta(seconds=1), error="原始故障")
    service.scan(now=NOW + timedelta(seconds=2))
    first = service.archive.connection.execute("SELECT * FROM reminder_events").fetchone()
    for index in range(100):
        fault(service, NOW + timedelta(minutes=1, seconds=index), error=f"另一个故障 {index}")
    display = FailureCenterService(service.archive).list_items()["items"]
    assert len(display) == 100
    assert all(i["job_id"] != original_job for i in display)
    assert service.scan(now=NOW + timedelta(minutes=5))["created"] == 100
    queued = service.archive.connection.execute("SELECT * FROM reminder_events WHERE event_id=?", (first["event_id"],)).fetchone()
    assert queued["in_app_status"] == "queued" and queued["windows_status"] == "queued"
    release = NOW.replace(hour=15, minute=59)
    service.scan(now=release)
    assert service.dispatch_windows(lambda *a: True, now=release)["sent"] == 20
    delivered = service.archive.connection.execute("SELECT * FROM reminder_events WHERE event_id=?", (first["event_id"],)).fetchone()
    assert delivered["in_app_status"] == "published" and delivered["windows_status"] == "sent"


def test_sink_unknown_result_is_visible_without_automatic_retry(service):
    values = rule()
    values["channels"] = ["windows"]
    service.save_rule(values, now=NOW)
    message(service, "one")
    message(service, "two")
    service.scan(now=NOW)
    calls = []
    result = service.dispatch_windows(lambda *a: calls.append(a), now=NOW)
    assert result["sent"] == 0 and result["failed"] == 0
    events = service.list_events()["items"]
    assert len(events) == 2
    assert all(e["delivery_status"]["windows"] == "unknown" for e in events)
    service.dispatch_windows(lambda *a: calls.append(a) or True, now=NOW + timedelta(days=1))
    assert len(calls) == 1
    service.retry_windows(events[0]["event_id"], now=NOW + timedelta(days=1))
    assert service.dispatch_windows(lambda *a: calls.append(a) or True, now=NOW + timedelta(days=1))["sent"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["keyword", "resource", "failure"])
@pytest.mark.parametrize("change", [dict(name="改名"), dict(quiet_start="01:00", quiet_end="01:00"),
                                   dict(channels=["windows"]), dict(group_ids=[456, 123])])
def test_presentation_and_channel_edits_preserve_unscanned_sources(service, kind, change):
    values = rule(kind)
    values["group_ids"] = [123, 456]
    if kind == "keyword":
        message(service, "historical")
    elif kind == "resource":
        candidate(service, "https://example.com/historical")
    else:
        fault(service, NOW - timedelta(minutes=1), error="历史故障")
    saved = service.save_rule(values, now=NOW)
    c = service.archive.connection
    columns = "message_cursor,candidate_cursor,failure_baseline,failure_seen"
    before = tuple(c.execute(f"SELECT {columns} FROM reminder_rules WHERE rule_id=?", (saved["rule_id"],)).fetchone())
    resource_before = [tuple(r) for r in c.execute("SELECT * FROM reminder_resource_baselines ORDER BY rule_id,group_id,link")]
    if kind == "keyword":
        message(service, "pending")
    elif kind == "resource":
        candidate(service, "https://example.com/pending")
    else:
        fault(service, NOW + timedelta(seconds=1), error="待扫描故障")
    values.update(change)
    updated = service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW + timedelta(minutes=1))
    assert updated["revision"] == saved["revision"] + 1
    after = tuple(c.execute(f"SELECT {columns} FROM reminder_rules WHERE rule_id=?", (saved["rule_id"],)).fetchone())
    assert after == before
    assert [tuple(r) for r in c.execute("SELECT * FROM reminder_resource_baselines ORDER BY rule_id,group_id,link")] == resource_before
    assert service.scan(now=NOW + timedelta(minutes=2))["created"] == 1


@pytest.mark.parametrize("kind", ["keyword", "resource", "failure"])
def test_disable_preserves_baseline_and_reenable_ignores_paused_sources(service, kind):
    values = rule(kind)
    saved = service.save_rule(values, now=NOW)
    c = service.archive.connection
    columns = "message_cursor,candidate_cursor,failure_baseline,failure_seen"
    before = tuple(c.execute(f"SELECT {columns} FROM reminder_rules WHERE rule_id=?", (saved["rule_id"],)).fetchone())
    if kind == "keyword":
        message(service, "before-disable")
    elif kind == "resource":
        candidate(service, "https://example.com/before-disable")
    else:
        fault(service, NOW + timedelta(seconds=1), error="暂停前故障")
    values["enabled"] = False
    disabled = service.save_rule(values, saved["rule_id"], saved["revision"], now=NOW + timedelta(minutes=1))
    assert tuple(c.execute(f"SELECT {columns} FROM reminder_rules WHERE rule_id=?", (saved["rule_id"],)).fetchone()) == before
    assert service.scan(now=NOW + timedelta(minutes=2))["created"] == 0
    values["enabled"] = True
    service.save_rule(values, saved["rule_id"], disabled["revision"], now=NOW + timedelta(minutes=3))
    assert service.scan(now=NOW + timedelta(minutes=4))["created"] == 0
    if kind == "keyword":
        message(service, "after-reenable")
    elif kind == "resource":
        candidate(service, "https://example.com/after-reenable")
    else:
        fault(service, NOW + timedelta(minutes=5), error="重开后新故障")
    assert service.scan(now=NOW + timedelta(minutes=6))["created"] == 1


@pytest.mark.parametrize("kind", ["keyword", "resource"])
def test_preview_reads_latest_bounded_sources_without_writing(service, kind):
    for index in range(205):
        if kind == "keyword":
            message(service, f"history-{index}", text=f"hello 最新消息 {index}")
        else:
            candidate(service, f"https://example.com/history-{index}")
    if kind == "resource":
        service.archive.connection.execute("UPDATE candidates SET title='最新资源 204' WHERE candidate_id=(SELECT MAX(candidate_id) FROM candidates)")
        service.archive.connection.commit()
    before = service.archive.connection.total_changes
    preview = service.preview(rule(kind), now=NOW)
    assert service.archive.connection.total_changes == before
    assert preview["total"] == 200
    assert preview["truncated"] is True
    assert len(preview["items"]) == 20
    assert "204" in preview["items"][0]["body" if kind == "keyword" else "title"]
