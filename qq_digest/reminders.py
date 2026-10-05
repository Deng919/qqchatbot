"""Persistent local reminder rules and safe, bounded desktop delivery.

The caller owns feature gating and the archive operation lock. No source collection,
network traffic or desktop notifications happen unless explicitly requested here.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .archive import Archive
from .failure_center import FailureCenterService


class ReminderConflict(ValueError):
    """An editor submitted an obsolete rule revision."""


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _link_key(value: str) -> str:
    value = value.strip()
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))
    except ValueError:
        return value


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(timezone.utc)


class ReminderService:
    def __init__(self, archive: Archive, timezone_name="Asia/Shanghai"):
        self.archive = archive
        self.connection = archive.connection
        self.timezone = ZoneInfo(timezone_name)

    def _validate(self, values):
        if not isinstance(values, dict):
            raise ValueError("规则必须为对象")
        allowed = {"name", "kind", "enabled", "group_ids", "channels", "keywords", "quiet_start", "quiet_end"}
        if set(values) - allowed:
            raise ValueError("未知规则字段")
        name = values.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("规则名称必须为 1 至 100 个字符")
        kind = values.get("kind")
        if kind not in ("keyword", "resource", "task_due", "failure"):
            raise ValueError("未知提醒类型")
        enabled = values.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("enabled 必须为布尔值")
        groups = values.get("group_ids", [])
        if not isinstance(groups, list) or len(groups) > 1000 or any(type(g) is not int or g <= 0 for g in groups):
            raise ValueError("群编号必须为正整数列表")
        groups = list(dict.fromkeys(groups))
        known = {r[0] for r in self.connection.execute("SELECT group_id FROM groups")}
        if set(groups) - known:
            raise ValueError("指定群不存在")
        channels = values.get("channels", ["in_app", "windows"])
        if not isinstance(channels, list) or not channels or any(c not in ("in_app", "windows") for c in channels):
            raise ValueError("至少选择一个有效提醒渠道")
        keywords = values.get("keywords", [])
        if not isinstance(keywords, list) or len(keywords) > 20 or any(not isinstance(k, str) or not k.strip() or len(k.strip()) > 100 for k in keywords):
            raise ValueError("关键词最多 20 个，每个为 1 至 100 个字符")
        keywords = list(dict.fromkeys(k.strip() for k in keywords))
        if kind == "keyword" and not keywords:
            raise ValueError("关键词提醒至少填写一个关键词")
        result = dict(name=name.strip(), kind=kind, enabled=enabled, group_ids=groups,
                      channels=list(dict.fromkeys(channels)), keywords=keywords)
        for field, default in (("quiet_start", "22:00"), ("quiet_end", "08:00")):
            value = values.get(field, default)
            if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError("免打扰时间必须为 HH:MM")
            result[field] = value
        return result

    @staticmethod
    def _id(value, label="编号"):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{label}必须为正整数")
        return value

    @staticmethod
    def _rule_dto(row):
        return dict(json.loads(row["values_json"]), rule_id=row["rule_id"], revision=row["revision"])

    def _rule(self, rule_id):
        self._id(rule_id)
        row = self.connection.execute("SELECT * FROM reminder_rules WHERE rule_id=? AND deleted=0", (rule_id,)).fetchone()
        if row is None:
            raise LookupError("提醒规则不存在")
        return row

    def list_rules(self):
        return {"rules": [self._rule_dto(r) for r in self.connection.execute("SELECT * FROM reminder_rules WHERE deleted=0 ORDER BY rule_id")]}

    def save_rule(self, values, rule_id=None, expected_revision=None, now=None):
        values, now = self._validate(values), _now(now)
        with self.archive.transaction():
            old_values = None
            if rule_id is not None:
                old = self._rule(rule_id)
                self._id(expected_revision, "revision")
                if old["revision"] != expected_revision:
                    raise ReminderConflict("规则已在其他窗口修改，请重新打开")
                old_values = json.loads(old["values_json"])
            conditions_changed = old_values is not None and (
                old_values["kind"] != values["kind"]
                or set(old_values["group_ids"]) != set(values["group_ids"])
                or old_values["keywords"] != values["keywords"]
            )
            reset_baseline = (old_values is None or conditions_changed
                              or (not old_values["enabled"] and values["enabled"]))
            if reset_baseline:
                message = self.connection.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
                candidate = self.connection.execute("SELECT COALESCE(MAX(candidate_id),0) FROM candidates").fetchone()[0]
                # Baseline fingerprints suppress automatic retries of faults
                # already present on creation, re-enable or condition change.
                failures = self._failures()
                seen = json.dumps([key for i in failures for key in (self._failure_identity(i), self._fingerprint(i))])
                params = (json.dumps(values, ensure_ascii=False), message, candidate, now.isoformat(), seen)
            if rule_id is None:
                rule_id = self.connection.execute("""INSERT INTO reminder_rules(values_json,message_cursor,candidate_cursor,failure_baseline,failure_seen)
                    VALUES(?,?,?,?,?)""", params).lastrowid
            elif reset_baseline:
                self.connection.execute("""UPDATE reminder_rules SET values_json=?,message_cursor=?,candidate_cursor=?,
                    failure_baseline=?,failure_seen=?,revision=revision+1 WHERE rule_id=?""", (*params, rule_id))
            else:
                self.connection.execute("UPDATE reminder_rules SET values_json=?,revision=revision+1 WHERE rule_id=?",
                                        (json.dumps(values, ensure_ascii=False), rule_id))
            if old_values is not None:
                if conditions_changed:
                    # A queue created by previous conditions is not part of the
                    # new rule's post-save population. Published history remains.
                    self.connection.execute("""UPDATE reminder_events SET
                        in_app_status=CASE WHEN in_app_status='queued' THEN 'canceled' ELSE in_app_status END,
                        windows_status=CASE WHEN windows_status IN ('queued','failed') THEN 'canceled' ELSE windows_status END
                        WHERE rule_id=?""", (rule_id,))
                for channel, column in (("in_app", "in_app_status"), ("windows", "windows_status")):
                    if channel not in values["channels"]:
                        self.connection.execute(f"UPDATE reminder_events SET {column}='disabled' WHERE rule_id=? AND {column} IN ('queued','failed')", (rule_id,))
            if reset_baseline:
                self.connection.execute("DELETE FROM reminder_resource_baselines WHERE rule_id=?", (rule_id,))
            if reset_baseline and values["kind"] == "resource":
                sources = self.connection.execute("""SELECT DISTINCT c.group_id,c.link FROM candidates c
                    JOIN groups g ON g.group_id=c.group_id WHERE c.candidate_type='resource'
                    AND c.status<>'ignored' AND TRIM(c.link)<>''""").fetchall()
                self.connection.executemany("INSERT OR IGNORE INTO reminder_resource_baselines(rule_id,group_id,link) VALUES(?,?,?)",
                    [(rule_id, source["group_id"], _link_key(source["link"])) for source in sources])
        return self._rule_dto(self._rule(rule_id))

    def delete_rule(self, rule_id, expected_revision):
        with self.archive.transaction():
            row = self._rule(rule_id)
            self._id(expected_revision, "revision")
            if row["revision"] != expected_revision:
                raise ReminderConflict("规则已在其他窗口修改，请重新打开")
            self.connection.execute("UPDATE reminder_rules SET deleted=1,revision=revision+1 WHERE rule_id=?", (rule_id,))
            self.connection.execute("""UPDATE reminder_events SET
                in_app_status=CASE WHEN in_app_status='queued' THEN 'canceled' ELSE in_app_status END,
                windows_status=CASE WHEN windows_status IN ('queued','failed') THEN 'canceled' ELSE windows_status END
                WHERE rule_id=?""", (rule_id,))
        return {"deleted": True}

    def _quiet(self, rule, now):
        start, end = rule["quiet_start"], rule["quiet_end"]
        clock = now.astimezone(self.timezone).strftime("%H:%M")
        return start != end and (start <= clock < end if start < end else clock >= start or clock < end)

    @staticmethod
    def _scope(rule, group_id):
        return not rule["group_ids"] or group_id in rule["group_ids"]

    def _failures(self):
        return [i for i in FailureCenterService(self.archive).list_items(unbounded=True)["items"] if i["status"] == "active"]

    @staticmethod
    def _fingerprint(item):
        text = json.dumps([item["kind"], item["group_id"], item["target_date"], item["stage"], item["error"]], ensure_ascii=False)
        return hashlib.sha256(text.encode()).hexdigest()

    def _failure_identity(self, item):
        return item["id"] + ":" + item["at"] + ":" + self._fingerprint(item)

    def _source_items(self, rule, now, row=None):
        """Yield source DTOs and a last processed cursor (preview omits row)."""
        kind = rule["kind"]
        group_names = {r[0]: r[1] for r in self.connection.execute("SELECT group_id,name FROM groups")}
        items, cursor = [], None
        if kind == "keyword":
            rows = self.connection.execute("SELECT rowid AS source_rowid,* FROM messages WHERE rowid>? ORDER BY rowid LIMIT 200" if row is not None else
                "SELECT rowid AS source_rowid,* FROM messages ORDER BY rowid DESC LIMIT 200", (row["message_cursor"],) if row is not None else ()).fetchall()
            for source in rows:
                cursor = source["source_rowid"]
                if self._scope(rule, source["group_id"]) and source["text"].strip() and any(_normalize(k) in _normalize(source["text"]) for k in rule["keywords"]):
                    items.append(dict(event_key=f"message:{cursor}", source_ref=str(cursor), group_id=source["group_id"],
                        title="关键词命中", body=source["text"][:500], source_url=f"/search?group_id={source['group_id']}"))
        elif kind == "resource":
            rows = self.connection.execute("SELECT * FROM candidates WHERE candidate_id>? ORDER BY candidate_id LIMIT 200" if row is not None else
                "SELECT * FROM candidates ORDER BY candidate_id DESC LIMIT 200", (row["candidate_cursor"],) if row is not None else ()).fetchall()
            links = set()
            baselines = {(r[0], r[1]) for r in self.connection.execute(
                "SELECT group_id,link FROM reminder_resource_baselines WHERE rule_id=?", (row["rule_id"],))} if row is not None else set()
            for source in rows:
                cursor = source["candidate_id"]
                if source["candidate_type"] != "resource" or source["status"] == "ignored" or not self._scope(rule, source["group_id"]):
                    continue
                link = _link_key(source["link"])
                if link and (source["group_id"], link) in baselines:
                    continue
                key = f"resource:{source['group_id']}:{link}" if link else f"candidate:{cursor}"
                if key in links:
                    continue
                links.add(key)
                items.append(dict(event_key=key, source_ref=str(cursor), group_id=source["group_id"],
                    title=source["title"][:200], body=source["content"][:500], source_url="/candidates"))
        elif kind == "task_due":
            today = now.astimezone(self.timezone).date().isoformat()
            for source in self.connection.execute("SELECT * FROM tasks WHERE status='open' AND due_date IS NOT NULL AND due_date<=? ORDER BY due_date,task_id", (today,)):
                if self._scope(rule, source["group_id"]):
                    items.append(dict(event_key=f"task:{source['task_id']}:{source['due_date']}", source_ref=json.dumps([source["task_id"], source["due_date"]]),
                        group_id=source["group_id"], title=source["title"][:200], body=f"截止日期：{source['due_date']}", source_url="/tasks"))
        else:
            known = set(json.loads(row["failure_seen"])) if row is not None else set()
            for source in self._failures():
                if not self._scope(rule, source["group_id"]):
                    continue
                if row is not None:
                    if self._failure_identity(source) in known or self._fingerprint(source) in known:
                        continue
                    try:
                        if _now(datetime.fromisoformat(source["at"])) < _now(datetime.fromisoformat(row["failure_baseline"])):
                            continue
                    except ValueError:
                        continue
                fingerprint = self._fingerprint(source)
                items.append(dict(event_key="failure:" + fingerprint, source_ref=fingerprint, group_id=source["group_id"],
                    title="运行故障", body="检测到尚未恢复的运行故障，请到运行记录／异常页查看详情。", source_url="/failures"))
        # Jobs can keep their historic group scope after that group is removed.
        # Such sources no longer belong to this archive's reminder population.
        items = [i for i in items if i["group_id"] is None or i["group_id"] in group_names]
        for item in items:
            item.update(kind=kind, rule_name=rule["name"], group_name=group_names.get(item["group_id"], "关联群未知"), created_at=now.isoformat())
        return items, cursor

    def preview(self, values, now=None):
        rule, now = self._validate(values), _now(now)
        items, _ = self._source_items(rule, now)
        truncated = False
        if rule["kind"] in ("keyword", "resource"):
            table, key = ("messages", "rowid") if rule["kind"] == "keyword" else ("candidates", "candidate_id")
            # Read at most 201 source positions; never scan all archived text just
            # to present a historical preview total.
            truncated = self.connection.execute(f"SELECT 1 FROM {table} ORDER BY {key} DESC LIMIT 1 OFFSET 200").fetchone() is not None
        return {"items": [{k: v for k, v in i.items() if k not in ("event_key", "source_ref")} for i in items[:20]],
                "total": len(items), "truncated": truncated, "quiet": self._quiet(rule, now), "channels": rule["channels"]}

    def _valid(self, event, now, active_failures=None):
        if event["kind"] == "task_due":
            task_id, due = json.loads(event["source_ref"])
            return self.connection.execute("SELECT 1 FROM tasks WHERE task_id=? AND status='open' AND due_date=? AND due_date<=?",
                (task_id, due, now.astimezone(self.timezone).date().isoformat())).fetchone() is not None
        if event["kind"] == "failure":
            active = active_failures if active_failures is not None else {self._fingerprint(i) for i in self._failures()}
            return event["source_ref"] in active
        return True

    def _release(self, now):
        active = None
        rows = self.connection.execute("""SELECT e.*,r.values_json,r.deleted FROM reminder_events e
            JOIN reminder_rules r ON r.rule_id=e.rule_id
            WHERE e.in_app_status='queued' OR e.windows_status IN ('queued','failed')""").fetchall()
        for event in rows:
            rule = json.loads(event["values_json"])
            if event["kind"] == "failure" and active is None:
                active = {self._fingerprint(i) for i in self._failures()}
            if event["kind"] != rule["kind"] or not self._scope(rule, event["group_id"]) or not self._valid(event, now, active):
                self.connection.execute("""UPDATE reminder_events SET
                    in_app_status=CASE WHEN in_app_status='queued' THEN 'canceled' ELSE in_app_status END,
                    windows_status=CASE WHEN windows_status IN ('queued','failed') THEN 'canceled' ELSE windows_status END WHERE event_id=?""", (event["event_id"],))
            elif not event["deleted"] and rule["enabled"] and "in_app" in rule["channels"] and not self._quiet(rule, now):
                self.connection.execute("UPDATE reminder_events SET in_app_status='published' WHERE event_id=? AND in_app_status='queued'", (event["event_id"],))

    def _queued_count(self):
        return self.connection.execute("""SELECT COUNT(*) FROM reminder_events WHERE in_app_status='queued'
            OR windows_status='queued' OR (windows_status='failed' AND windows_attempts<3)""").fetchone()[0]

    def scan(self, now=None):
        now, created = _now(now), 0
        with self.archive.transaction():
            for row in self.connection.execute("SELECT * FROM reminder_rules WHERE deleted=0 ORDER BY rule_id").fetchall():
                rule = self._rule_dto(row)
                if not rule["enabled"]:
                    continue
                items, cursor = self._source_items(rule, now, row)
                for item in items:
                    in_app = "queued" if "in_app" in rule["channels"] else "disabled"
                    windows = "queued" if "windows" in rule["channels"] else "disabled"
                    result = self.connection.execute("""INSERT OR IGNORE INTO reminder_events(rule_id,event_key,kind,rule_name,title,body,
                        group_id,group_name,source_url,source_ref,created_at,in_app_status,windows_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (row["rule_id"], item["event_key"], item["kind"], item["rule_name"], item["title"], item["body"], item["group_id"], item["group_name"],
                         item["source_url"], item["source_ref"], item["created_at"], in_app, windows))
                    created += result.rowcount
                if cursor is not None:
                    column = "message_cursor" if rule["kind"] == "keyword" else "candidate_cursor"
                    self.connection.execute(f"UPDATE reminder_rules SET {column}=? WHERE rule_id=?", (cursor, row["rule_id"]))
            self._release(now)
        return {"created": created, "queued": self._queued_count()}

    @staticmethod
    def _event_dto(row):
        fields = ("event_id", "rule_id", "rule_name", "kind", "title", "body", "group_id", "group_name", "source_url", "created_at")
        return dict({k: row[k] for k in fields}, read=bool(row["read"]),
                    delivery_status={"in_app": row["in_app_status"], "windows": row["windows_status"]}, delivery_error=row["delivery_error"])

    def list_events(self, page=1, page_size=20, unread_only=False):
        self._id(page, "页码")
        self._id(page_size, "每页数量")
        if page_size > 100 or (page-1)*page_size > 2**63-1 or type(unread_only) is not bool:
            raise ValueError("分页／筛选参数无效")
        visible = "(in_app_status='published' OR windows_status IN ('sent','failed','unknown'))"
        where = visible + (" AND read=0" if unread_only else "")
        total = self.connection.execute(f"SELECT COUNT(*) FROM reminder_events WHERE {where}").fetchone()[0]
        unread = self.connection.execute(f"SELECT COUNT(*) FROM reminder_events WHERE {visible} AND read=0").fetchone()[0]
        rows = self.connection.execute(f"SELECT * FROM reminder_events WHERE {where} ORDER BY event_id DESC LIMIT ? OFFSET ?", (page_size, (page-1)*page_size))
        return {"items": [self._event_dto(r) for r in rows], "total": total, "unread": unread, "queued": self._queued_count(), "page": page, "page_size": page_size}

    def mark_read(self, event_id, read=True):
        self._id(event_id)
        if type(read) is not bool:
            raise ValueError("read 必须为布尔值")
        with self.archive.transaction():
            row = self.connection.execute("SELECT * FROM reminder_events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise LookupError("提醒不存在")
            self.connection.execute("UPDATE reminder_events SET read=? WHERE event_id=?", (int(read), event_id))
        return self._event_dto(self.connection.execute("SELECT * FROM reminder_events WHERE event_id=?", (event_id,)).fetchone())

    def dispatch_windows(self, sink, now=None):
        now = _now(now)
        with self.archive.transaction():
            # A crash between submission and acknowledgement must not resend automatically.
            self.connection.execute("UPDATE reminder_events SET windows_status='unknown',delivery_error='上次投递结果未知' WHERE windows_status='sending'")
            self._release(now)
            eligible = []
            if sink is not None:
                for event in self.connection.execute("""SELECT e.*,r.values_json,r.deleted FROM reminder_events e JOIN reminder_rules r ON r.rule_id=e.rule_id
                    WHERE e.windows_status IN ('queued','failed') AND e.windows_attempts<3
                    AND (e.next_attempt_at IS NULL OR e.next_attempt_at<=?) ORDER BY e.event_id""", (now.isoformat(),)).fetchall():
                    rule = json.loads(event["values_json"])
                    if not event["deleted"] and rule["enabled"] and "windows" in rule["channels"] and not self._quiet(rule, now):
                        eligible.append(event)
                        if len(eligible) == 20:
                            break
            for event in eligible:
                self.connection.execute("UPDATE reminder_events SET windows_status='sending',windows_attempts=windows_attempts+1 WHERE event_id=?", (event["event_id"],))
        if not eligible:
            return {"sent": 0, "failed": 0, "queued": self._queued_count()}
        counts = Counter(e["rule_name"] for e in eligible)
        body = "；".join(f"{name}：{count} 条" for name, count in counts.items())[:220] + "。打开提醒列表查看。"
        try:
            outcome = sink("QQ Digest 提醒", body)
        except Exception:
            outcome = False
        unknown = outcome is None
        accepted = bool(outcome)
        with self.archive.transaction():
            for event in eligible:
                attempts = event["windows_attempts"] + 1
                self.connection.execute("""UPDATE reminder_events SET windows_status=?,next_attempt_at=?,delivery_error=?
                    WHERE event_id=? AND windows_status='sending'""", ("unknown" if unknown else "sent" if accepted else "failed",
                    None if unknown or accepted or attempts >= 3 else (now + timedelta(minutes=2 ** attempts)).isoformat(),
                    "Windows 投递结果未知" if unknown else "" if accepted else "Windows 未接受通知请求", event["event_id"]))
        return {"sent": len(eligible) if accepted else 0, "failed": 0 if accepted or unknown else len(eligible), "queued": self._queued_count()}

    def retry_windows(self, event_id, now=None):
        self._id(event_id)
        now = _now(now)
        with self.archive.transaction():
            event = self.connection.execute("SELECT * FROM reminder_events WHERE event_id=?", (event_id,)).fetchone()
            if event is None:
                raise LookupError("提醒不存在")
            rule = self._rule_dto(self._rule(event["rule_id"]))
            if event["windows_status"] not in ("failed", "unknown"):
                raise ValueError("此提醒不需要重新投递")
            if (not rule["enabled"] or "windows" not in rule["channels"] or event["kind"] != rule["kind"]
                    or not self._scope(rule, event["group_id"]) or not self._valid(event, now)):
                raise ValueError("此提醒当前不可投递")
            self.connection.execute("UPDATE reminder_events SET windows_status='queued',windows_attempts=0,next_attempt_at=NULL,delivery_error='' WHERE event_id=?", (event_id,))
        return self._event_dto(self.connection.execute("SELECT * FROM reminder_events WHERE event_id=?", (event_id,)).fetchone())
