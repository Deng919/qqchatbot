"""Sourced task suggestions and user-confirmed work items."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .report_sources import load_verified_report_sources


class TaskInboxService:
    def __init__(self, archive: Archive, *, timezone_name: str):
        self.archive = archive
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)

    def suggestions(self, *, now: datetime | None = None) -> list[dict]:
        today = self._now(now).astimezone(self.timezone).date()
        rows = self.archive.connection.execute(
            "SELECT r.report_id,r.group_id,r.report_date,r.json_path,g.name AS group_name "
            "FROM reports r JOIN groups g ON g.group_id=r.group_id "
            "WHERE r.report_date BETWEEN ? AND ? ORDER BY r.report_date DESC,r.report_id DESC",
            ((today - timedelta(days=29)).isoformat(), today.isoformat()),
        ).fetchall()
        decided = {row["suggestion_key"] for row in self.archive.connection.execute(
            "SELECT suggestion_key FROM task_suggestion_decisions"
        ).fetchall()}
        results: list[dict] = []
        for report in rows:
            try:
                payload = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("evidence_version") != 1:
                    continue
                raw_tasks = payload.get("tasks")
                if not isinstance(raw_tasks, list):
                    continue
                evidence = load_verified_report_sources(
                    self.archive.connection, report["json_path"],
                    group_id=report["group_id"],
                    start_date=report["report_date"], end_date=report["report_date"],
                    timezone_name=self.timezone_name,
                )
                if evidence is None:
                    continue
            except (OSError, UnicodeError, ValueError, TypeError, KeyError):
                continue
            task_evidence = [item for item in evidence if item["section"] == "任务或承诺"]
            report_tasks = [item for item in raw_tasks if isinstance(item, dict)]
            for index, (raw, cited) in enumerate(zip(report_tasks, task_evidence)):
                if (not isinstance(raw.get("owner"), str)
                        or not isinstance(raw.get("description"), str)
                        or not raw["description"].strip()):
                    continue
                source_ids = list(cited["source_ids"])
                if not source_ids:
                    continue
                owner = raw["owner"].strip()
                title = raw["description"].strip()
                deadline_hint = raw.get("deadline", "")
                if not isinstance(deadline_hint, str):
                    deadline_hint = ""
                material = json.dumps(
                    [report["report_id"], index, owner, title, deadline_hint.strip(), source_ids],
                    ensure_ascii=False, separators=(",", ":"),
                )
                key = hashlib.sha256(material.encode("utf-8")).hexdigest()
                if key in decided:
                    continue
                results.append({
                    "key": key, "title": title, "owner": owner,
                    "deadline_hint": deadline_hint.strip(),
                    "group_id": report["group_id"], "group_name": report["group_name"],
                    "report_id": report["report_id"], "report_date": report["report_date"],
                    "source_ids": source_ids,
                    "report_url": f"/reports?kind=daily&id={report['report_id']}",
                })
        return results

    def decide(self, key: str, *, action: str, title: str = "", owner: str = "",
               due_date: str | None = None, now: datetime | None = None) -> dict | None:
        if action not in {"confirm", "ignore"}:
            raise ValueError("处理方式无效")
        if self.archive.connection.execute(
            "SELECT 1 FROM task_suggestion_decisions WHERE suggestion_key=?", (key,)
        ).fetchone():
            raise ValueError("建议已处理")
        suggestion = next((row for row in self.suggestions(now=now) if row["key"] == key), None)
        if suggestion is None:
            raise ValueError("建议不存在或来源已失效")
        timestamp = self._now(now).isoformat()
        if action == "ignore":
            with self.archive.transaction():
                self.archive.connection.execute(
                    "INSERT INTO task_suggestion_decisions(suggestion_key,group_id,decision,task_id,decided_at) "
                    "VALUES (?,?,'ignored',NULL,?)", (key, suggestion["group_id"], timestamp),
                )
            return None
        title, owner, due_date = self._fields(title, owner, due_date)
        try:
            with self.archive.transaction():
                cursor = self.archive.connection.execute(
                    "INSERT INTO tasks(group_id,title,owner,due_date,status,source_ids,primary_source_id,source_report_id,created_at,updated_at) "
                    "VALUES (?,?,?,?,'open',?,?,?,?,?)",
                    (suggestion["group_id"], title, owner, due_date,
                     json.dumps(suggestion["source_ids"], ensure_ascii=False),
                     suggestion["source_ids"][0], suggestion["report_id"], timestamp, timestamp),
                )
                task_id = cursor.lastrowid
                self.archive.connection.execute(
                    "INSERT INTO task_suggestion_decisions(suggestion_key,group_id,decision,task_id,decided_at) "
                    "VALUES (?,?,'confirmed',?,?)",
                    (key, suggestion["group_id"], task_id, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("建议已处理或来源已失效，请刷新后重试") from exc
        return self._get_task(task_id, now=now)

    def create_from_message(self, *, group_id: int, msg_id: str, title: str,
                            owner: str = "", due_date: str | None = None,
                            now: datetime | None = None) -> dict:
        title, owner, due_date = self._fields(title, owner, due_date)
        if not isinstance(msg_id, str) or not msg_id.strip() or not self.archive.connection.execute(
            "SELECT 1 FROM messages WHERE group_id=? AND msg_id=?", (group_id, msg_id)
        ).fetchone():
            raise ValueError("原消息不存在或不属于该群")
        timestamp = self._now(now).isoformat()
        try:
            with self.archive.transaction():
                cursor = self.archive.connection.execute(
                    "INSERT INTO tasks(group_id,title,owner,due_date,status,source_ids,primary_source_id,source_report_id,created_at,updated_at) "
                    "VALUES (?,?,?,?,'open',?,?,NULL,?,?)",
                    (group_id, title, owner, due_date,
                     json.dumps([msg_id], ensure_ascii=False), msg_id, timestamp, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("来源消息已失效，请重新搜索后创建") from exc
        return self._get_task(cursor.lastrowid, now=now)

    def list_tasks(self, *, status: str = "open", now: datetime | None = None) -> dict:
        if status not in {"open", "completed", "canceled", "all"}:
            raise ValueError("状态无效")
        rows = self.archive.connection.execute(
            "SELECT t.*,g.name AS group_name FROM tasks t "
            "LEFT JOIN groups g ON g.group_id=t.group_id ORDER BY t.task_id DESC"
        ).fetchall()
        items = [self._present(row, now=now) for row in rows]
        counts = {
            "open": sum(item["status"] == "open" for item in items),
            "today": sum(item["due_bucket"] == "today" for item in items),
            "overdue": sum(item["due_bucket"] == "overdue" for item in items),
        }
        if status != "all":
            items = [item for item in items if item["status"] == status]
        if status == "open":
            priority = {"overdue": 0, "today": 1, "upcoming": 2, "none": 3}
            items.sort(key=lambda item: (priority[item["due_bucket"]],
                                         item["due_date"] or "9999-12-31", -item["task_id"]))
        return {"items": items, "counts": counts}

    def update_task(self, task_id: int, *, title: str, owner: str = "",
                    due_date: str | None = None, status: str = "open",
                    now: datetime | None = None) -> dict:
        if status not in {"open", "completed", "canceled"}:
            raise ValueError("状态无效")
        title, owner, due_date = self._fields(title, owner, due_date)
        timestamp = self._now(now).isoformat()
        with self.archive.transaction():
            cursor = self.archive.connection.execute(
                "UPDATE tasks SET title=?,owner=?,due_date=?,status=?,updated_at=? WHERE task_id=?",
                (title, owner, due_date, status, timestamp, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("待办不存在")
        return self._get_task(task_id, now=now)

    def _get_task(self, task_id: int, *, now: datetime | None = None) -> dict:
        row = self.archive.connection.execute(
            "SELECT t.*,g.name AS group_name FROM tasks t "
            "LEFT JOIN groups g ON g.group_id=t.group_id WHERE t.task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise ValueError("待办不存在")
        return self._present(row, now=now)

    def _present(self, row, *, now: datetime | None = None) -> dict:
        today = self._now(now).astimezone(self.timezone).date().isoformat()
        due_date = row["due_date"]
        bucket = "none"
        if row["status"] == "open" and due_date:
            bucket = "overdue" if due_date < today else "today" if due_date == today else "upcoming"
        return {
            "task_id": row["task_id"], "title": row["title"], "owner": row["owner"],
            "due_date": due_date, "status": row["status"], "due_bucket": bucket,
            "group_id": row["group_id"], "group_name": row["group_name"] or str(row["group_id"]),
            "source_ids": json.loads(row["source_ids"]),
            "source_report_id": row["source_report_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @staticmethod
    def _fields(title: str, owner: str, due_date: str | None) -> tuple[str, str, str | None]:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 300:
            raise ValueError("待办标题须为 1 至 300 字")
        if not isinstance(owner, str) or len(owner.strip()) > 100:
            raise ValueError("负责人最多 100 字")
        if due_date in (None, ""):
            return title.strip(), owner.strip(), None
        if not isinstance(due_date, str):
            raise ValueError("截止日期必须是 YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(due_date)
        except ValueError as exc:
            raise ValueError("截止日期必须是 YYYY-MM-DD") from exc
        if parsed.isoformat() != due_date:
            raise ValueError("截止日期必须是 YYYY-MM-DD")
        return title.strip(), owner.strip(), due_date

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return result.astimezone(timezone.utc)
