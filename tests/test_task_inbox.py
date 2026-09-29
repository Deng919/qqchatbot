import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.task_inbox import TaskInboxService


NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def setup_inbox(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="项目群"), GroupConfig(group_id=456, name="其他群")])
    archive.ingest([
        NormalizedMessage(msg_id="m1", group_id=123, timestamp=NOW,
                          collected_at=NOW, text="小王明天完成文档"),
        NormalizedMessage(msg_id="other", group_id=456, timestamp=NOW,
                          collected_at=NOW, text="其他群消息"),
    ])
    return archive, TaskInboxService(archive, timezone_name="Asia/Shanghai")


def add_report(archive, tmp_path, *, evidence_version=1, sources=None, date="2026-09-29"):
    path = tmp_path / f"report-{date}-{evidence_version}.json"
    payload = {
        "evidence_version": evidence_version,
        "overview": "", "main_topics": [], "conclusions": [], "resources": [],
        "tasks": [{"owner": "小王", "description": "完成文档", "deadline": "明天",
                   "message_ids": ["m1"] if sources is None else sources}],
        "open_questions": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return archive.record_report(group_id=123, report_date=date,
                                 markdown_path=tmp_path / "report.md", json_path=path,
                                 candidate_ids=[])


def test_suggestions_require_verified_message_and_keep_deadline_as_hint(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    report_id = add_report(archive, tmp_path)
    suggestions = inbox.suggestions(now=NOW)
    assert len(suggestions) == 1
    assert suggestions[0]["report_id"] == report_id
    assert suggestions[0]["source_ids"] == ["m1"]
    assert suggestions[0]["deadline_hint"] == "明天"
    assert suggestions[0]["group_name"] == "项目群"


def test_malformed_report_task_cannot_borrow_another_tasks_citation(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps({
        "evidence_version": 1, "overview": "", "main_topics": [], "conclusions": [],
        "resources": [], "open_questions": [],
        "tasks": [
            {"owner": 42, "description": "坏任务", "message_ids": ["m1"]},
            {"owner": "小王", "description": "好任务", "message_ids": ["missing"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    archive.record_report(group_id=123, report_date="2026-09-29",
                          markdown_path=tmp_path / "bad.md", json_path=path,
                          candidate_ids=[])
    assert inbox.suggestions(now=NOW) == []


@pytest.mark.parametrize("sources,evidence", [(["other"], 1), (["missing"], 1), (["m1"], 0)])
def test_unverified_or_legacy_report_tasks_are_not_suggestions(tmp_path, sources, evidence):
    archive, inbox = setup_inbox(tmp_path)
    add_report(archive, tmp_path, evidence_version=evidence, sources=sources)
    assert inbox.suggestions(now=NOW) == []


def test_confirm_suggestion_is_once_only_and_keeps_source(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    add_report(archive, tmp_path)
    suggestion = inbox.suggestions(now=NOW)[0]
    task = inbox.decide(suggestion["key"], action="confirm", title="整理最终文档",
                        owner="小王", due_date="2026-09-30", now=NOW)
    assert task["status"] == "open"
    assert task["title"] == "整理最终文档"
    assert task["source_ids"] == ["m1"]
    assert inbox.suggestions(now=NOW) == []
    with pytest.raises(ValueError, match="已处理"):
        inbox.decide(suggestion["key"], action="confirm", title="重复",
                     owner="", due_date=None, now=NOW)


def test_direct_message_task_validates_source_and_due_date(tmp_path):
    _, inbox = setup_inbox(tmp_path)
    task = inbox.create_from_message(group_id=123, msg_id="m1", title="检查文档",
                                     owner="小王", due_date="2026-09-29", now=NOW)
    assert task["source_ids"] == ["m1"]
    assert task["due_bucket"] == "today"
    with pytest.raises(ValueError, match="原消息"):
        inbox.create_from_message(group_id=123, msg_id="other", title="跨群",
                                  owner="", due_date=None, now=NOW)
    with pytest.raises(ValueError, match="日期"):
        inbox.create_from_message(group_id=123, msg_id="m1", title="坏日期",
                                  owner="", due_date="明天", now=NOW)


def test_update_preserves_source_and_classifies_overdue(tmp_path):
    _, inbox = setup_inbox(tmp_path)
    task = inbox.create_from_message(group_id=123, msg_id="m1", title="检查文档",
                                     owner="", due_date="2026-09-28", now=NOW)
    assert inbox.list_tasks(now=NOW)["counts"]["overdue"] == 1
    edited = inbox.update_task(task["task_id"], title="完成文档", owner="小王",
                               due_date="2026-09-30", status="open", now=NOW)
    assert edited["source_ids"] == ["m1"]
    assert edited["due_bucket"] == "upcoming"
    done = inbox.update_task(task["task_id"], title="完成文档", owner="小王",
                             due_date="2026-09-30", status="completed", now=NOW)
    assert done["status"] == "completed"
    assert inbox.list_tasks(now=NOW)["counts"]["open"] == 0
    with pytest.raises(ValueError, match="状态"):
        inbox.update_task(task["task_id"], title="完成文档", owner="小王",
                          due_date=None, status="unknown", now=NOW)


def test_ignore_suggestion_hides_it_without_creating_task(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    add_report(archive, tmp_path)
    key = inbox.suggestions(now=NOW)[0]["key"]
    assert inbox.decide(key, action="ignore", now=NOW) is None
    assert inbox.suggestions(now=NOW) == []
    assert inbox.list_tasks(now=NOW)["items"] == []


def test_changed_report_deadline_surfaces_a_new_suggestion(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    add_report(archive, tmp_path)
    first = inbox.suggestions(now=NOW)[0]
    inbox.decide(first["key"], action="ignore", now=NOW)
    report = archive.report_for(123, "2026-09-29")
    path = tmp_path / "report-2026-09-29-1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tasks"][0]["deadline"] = "下周一"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    second = inbox.suggestions(now=NOW)
    assert report is not None
    assert len(second) == 1
    assert second[0]["deadline_hint"] == "下周一"
    assert second[0]["key"] != first["key"]


def test_deleting_group_removes_tasks_and_their_decisions(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    add_report(archive, tmp_path)
    key = inbox.suggestions(now=NOW)[0]["key"]
    inbox.decide(key, action="confirm", title="完成文档", now=NOW)
    archive.delete_group(123)
    assert archive.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert archive.connection.execute("SELECT COUNT(*) FROM task_suggestion_decisions").fetchone()[0] == 0


def test_database_rejects_task_when_primary_source_was_deleted(tmp_path):
    archive, _ = setup_inbox(tmp_path)
    with archive.transaction():
        archive.connection.execute("DELETE FROM messages WHERE group_id=123 AND msg_id='m1'")
    with pytest.raises(sqlite3.IntegrityError):
        with archive.transaction():
            archive.connection.execute(
                "INSERT INTO tasks(group_id,title,owner,due_date,status,source_ids,primary_source_id,source_report_id,created_at,updated_at) "
                "VALUES (123,'检查','',NULL,'open','[\"m1\"]','m1',NULL,?,?)",
                (NOW.isoformat(), NOW.isoformat()),
            )


def test_message_disappearing_during_creation_returns_source_error(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    with archive.transaction():
        archive.connection.execute(
            "CREATE TRIGGER remove_source_before_task BEFORE INSERT ON tasks "
            "BEGIN DELETE FROM messages WHERE group_id=123 AND msg_id='m1'; END"
        )
    with pytest.raises(ValueError, match="来源"):
        inbox.create_from_message(group_id=123, msg_id="m1", title="检查文档",
                                  now=NOW)
    assert archive.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
