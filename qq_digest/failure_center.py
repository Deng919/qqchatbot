"""Read a bounded, human-readable view of persisted operational failures."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from .archive import Archive


class FailureCenterService:
    def __init__(self, archive: Archive, *, connection=None):
        self.archive = archive
        self.connection = connection if connection is not None else archive.connection

    @staticmethod
    def _text(value: object, limit: int | None = 500) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _group_failures(error: str, *, unbounded: bool = False) -> list[dict]:
        try:
            value = json.loads(error)
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in (value if unbounded else value[:100]) if isinstance(item, dict)]

    def _failure_details(self, job, *, unbounded: bool = False) -> list[dict]:
        failures = self._group_failures(job["error"], unbounded=unbounded)
        if failures:
            return failures
        if job["job_type"] == "message_sync":
            rows = self.connection.execute(
                """SELECT group_id FROM job_group_results
                   WHERE job_id=? AND status='failed' ORDER BY group_id""",
                (job["job_id"],),
            ).fetchall()
            if rows:
                return [{"group_id": row["group_id"], "stage": "collection",
                         "error": job["error"]} for row in rows]
        return [{"error": job["error"] or "任务未完成"}]

    def _item_recovered(self, job, group_id: int | None) -> bool:
        connection = self.connection
        if group_id is not None:
            return connection.execute(
                """SELECT 1 FROM jobs newer
                   JOIN job_group_results outcome ON outcome.job_id=newer.job_id
                   WHERE newer.job_id>? AND newer.job_type=?
                     AND newer.target_date IS ? AND outcome.group_id=?
                     AND outcome.status IN ('success','skipped') LIMIT 1""",
                (job["job_id"], job["job_type"], job["target_date"], group_id),
            ).fetchone() is not None
        # Legacy sync failures have no reliable group scope. Keep them visible.
        if job["job_type"] == "message_sync" or not job["target_date"]:
            return False
        return connection.execute(
            """SELECT 1 FROM jobs WHERE job_id>? AND job_type=?
               AND target_date=? AND status='success' LIMIT 1""",
            (job["job_id"], job["job_type"], job["target_date"]),
        ).fetchone() is not None

    def job_is_recovered(self, job) -> bool:
        failures = self._failure_details(job)
        return bool(failures) and all(
            self._item_recovered(
                job, failure.get("group_id") if type(failure.get("group_id")) is int else None
            ) for failure in failures
        )

    def list_items(self, *, unbounded: bool = False) -> dict:
        """The UI stays bounded; durable consumers can inspect every source.

        A source falling off a display page does not mean that it recovered.
        Unbounded mode also preserves complete reasons for internal fingerprints.
        Callers must not copy those reasons into user-facing reminder text.
        """
        connection = self.connection
        limit = "" if unbounded else "LIMIT 100"
        groups = {
            row["group_id"]: row["name"]
            for row in connection.execute("SELECT group_id,name FROM groups")
        }
        jobs = connection.execute(
            f"""SELECT * FROM jobs WHERE status IN ('failed','partial_success')
               ORDER BY job_id DESC {limit}"""
        ).fetchall()

        items: list[dict] = []
        for job in jobs:
            failures = self._failure_details(job, unbounded=unbounded)
            retry = connection.execute(
                """SELECT job_id,status FROM jobs WHERE retry_of_job_id=?
                   ORDER BY job_id DESC LIMIT 1""",
                (job["job_id"],),
            ).fetchone()
            for index, failure in enumerate(failures):
                raw_gid = failure.get("group_id")
                group_id = raw_gid if type(raw_gid) is int else None
                recovered = self._item_recovered(job, group_id)
                retrying = bool(retry and retry['status'] == 'running')
                group_name = self._text(
                    failure.get("group_name") or groups.get(group_id) or "全部已启用群", 100
                )
                kind = job["job_type"]
                items.append({
                    "id": f"job:{job['job_id']}:{group_id if group_id is not None else index}",
                    "kind": kind,
                    "job_id": job["job_id"],
                    "group_id": group_id,
                    "group_name": group_name,
                    "target_date": job["target_date"] or "",
                    "stage": self._text(failure.get("stage") or kind, None),
                    "error": self._text(failure.get("error") or job["error"], None),
                    "at": job["finished_at"] or job["started_at"] or "",
                    "status": "recovered" if recovered else "pending" if retrying else "active",
                    "action": "retry_job" if (
                        kind == "message_sync" or
                        (kind == "daily_digest" and bool(job["target_date"]))
                    ) and not recovered and not retrying else "",
                    "next_step": (
                        "该群的后续任务已成功" if recovered and group_id is not None else
                        "较新的同日期任务已成功" if recovered else
                        "等待重试结果" if retrying else
                        "旧任务缺少报告日期，请在总览重新运行" if kind == "daily_digest" and not job["target_date"] else
                        "检查 AI 设置与采集状态后重试" if kind == "daily_digest" else
                        "检查本地 QQ 数据库后重试导入" if kind == "message_sync" else
                        "查看任务日志并修复原因"
                    ),
                    "retry_job_id": retry["job_id"] if retry else None,
                    "retry_status": retry["status"] if retry else "",
                })

        sync_rows = connection.execute(
            f"""SELECT s.group_id,s.status,s.error,s.updated_at,g.name
               FROM sync_state s LEFT JOIN groups g ON g.group_id=s.group_id
               WHERE s.error<>'' AND s.status NOT IN ('active','manual_repair_completed')
               ORDER BY s.updated_at DESC {limit}"""
        ).fetchall()
        today = date.today()
        for row in sync_rows:
            group_id = row["group_id"]
            items.append({
                "id": f"sync:{group_id}", "kind": "group_sync",
                "job_id": None, "group_id": group_id,
                "group_name": row["name"] or str(group_id),
                "target_date": "", "stage": "collection",
                "error": self._text(row["error"], None), "at": row["updated_at"],
                "status": "active", "action": "collect",
                "action_url": "/collect?" + urlencode({
                    "group_id": group_id,
                    "start": (today - timedelta(days=7)).isoformat(),
                    "repair": "1",
                }),
                "next_step": "检查本地 QQ 数据库，并按日期范围检查与补采",
                "retry_job_id": None, "retry_status": "",
            })

        send_columns = ("send_id,status,error,attempted_at,updated_at,payload_json,"
                        "retry_count,manual_retry_count,report_id,channel,recipient")
        active_sends = connection.execute(
            f"""SELECT {send_columns} FROM send_log
                WHERE status='failed' OR
                      (status IN ('pending_send','sending') AND error<>'')
                ORDER BY send_id DESC {limit}"""
        ).fetchall()
        recovered_sends = connection.execute(
            f"""SELECT {send_columns} FROM send_log
                WHERE status='success' AND (retry_count>0 OR manual_retry_count>0)
                ORDER BY send_id DESC {limit}"""
        ).fetchall()
        sends = [*active_sends, *(recovered_sends if unbounded else recovered_sends[:max(0, 100 - len(active_sends))])]
        for row in sends:
            try:
                payload = json.loads(row["payload_json"] or "{}")
                if not isinstance(payload, dict):
                    payload = {}
            except (TypeError, ValueError):
                payload = {}
            equivalent = None
            if row["status"] == "failed":
                equivalent = connection.execute(
                    """SELECT send_id,status FROM send_log
                       WHERE send_id>? AND report_id IS ? AND channel=? AND recipient=?
                         AND status IN ('pending_send','sending','success')
                       ORDER BY send_id DESC LIMIT 1""",
                    (row["send_id"], row["report_id"], row["channel"], row["recipient"]),
                ).fetchone()
            status = (
                "recovered" if row["status"] == "success" or
                               (equivalent and equivalent["status"] == "success") else
                "pending" if row["status"] != "failed" or equivalent else "active"
            )
            items.append({
                "id": f"send:{row['send_id']}", "kind": "notification",
                "send_id": row["send_id"], "job_id": None,
                "group_id": None,
                "group_name": self._text(payload.get("group_name") or "关联群未知", 100),
                "target_date": self._text(payload.get("report_date"), None),
                "stage": "qq_bot", "error": self._text(row["error"], None),
                "at": row["updated_at"] or row["attempted_at"],
                "status": status,
                "action": "retry_notification" if status == "active" else "",
                "next_step": (
                    "检查 QQ Bot 配置后重新发送" if status == "active" else
                    "已有同一通知等待发送" if equivalent and status == "pending" else
                    "已重新排队，等待发送结果" if status == "pending" else
                    "通知已发送成功"
                ),
                "manual_retry_count": row["manual_retry_count"],
                "retry_job_id": None, "retry_status": "",
            })

        ignored = {row['source_id']: row['fingerprint'] for row in
                   connection.execute('SELECT source_id,fingerprint FROM failure_ignores')}
        for item in items:
            # Hash complete reasons before applying presentation limits.
            raw = json.dumps([item.get(key) for key in (
                'id', 'kind', 'group_id', 'target_date', 'stage', 'error', 'at'
            )], ensure_ascii=False, separators=(',', ':'))
            item['fingerprint'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()
            if item['status'] == 'active' and ignored.get(item['id']) == item['fingerprint']:
                item.update(status='ignored', action='', next_step='可恢复待处理')
            if not unbounded:
                item['error'] = self._text(item['error'], 500)
                item['stage'] = self._text(item['stage'], 50)
                item['target_date'] = self._text(item['target_date'], 20)
        items.sort(key=lambda item: item["at"], reverse=True)
        items.sort(key=lambda item: {"active": 0, "pending": 1, "ignored": 2, "recovered": 3}[item["status"]])
        return {
            "items": items if unbounded else items[:400],
            "counts": {
                "active": sum(item["status"] == "active" for item in items),
                "pending": sum(item["status"] == "pending" for item in items),
                "recovered": sum(item["status"] == "recovered" for item in items),
                "ignored": sum(item["status"] == "ignored" for item in items),
            },
        }

    def ignore_items(self, selectors: list[dict]) -> dict:
        return self._set_ignored(selectors, restore=False)

    def restore_items(self, selectors: list[dict]) -> dict:
        return self._set_ignored(selectors, restore=True)

    def _set_ignored(self, selectors: list[dict], *, restore: bool) -> dict:
        """Validate a displayed snapshot and mutate it under one SQLite write lock.

        A separate file connection keeps this transaction independent of the
        shared archive connection used by running jobs. No task results change.
        """
        path = next(row['file'] for row in self.connection.execute('PRAGMA database_list') if row['name'] == 'main')
        connection = sqlite3.connect(path, timeout=30) if path else self.connection
        connection.row_factory = sqlite3.Row
        unique = {item['id']: item['fingerprint'] for item in selectors}
        changed = 0
        try:
            connection.execute('BEGIN IMMEDIATE' if path else 'SAVEPOINT failure_ignore')
            current = {item['id']: item for item in
                       FailureCenterService(self.archive, connection=connection).list_items(unbounded=True)['items']}
            for source_id, fingerprint in unique.items():
                item = current.get(source_id)
                if not item or item['fingerprint'] != fingerprint or item['status'] != ('ignored' if restore else 'active'):
                    continue
                if restore:
                    connection.execute('DELETE FROM failure_ignores WHERE source_id=? AND fingerprint=?', (source_id, fingerprint))
                else:
                    connection.execute('''INSERT INTO failure_ignores(source_id,fingerprint,ignored_at) VALUES(?,?,?)
                        ON CONFLICT(source_id) DO UPDATE SET fingerprint=excluded.fingerprint,ignored_at=excluded.ignored_at''',
                        (source_id, fingerprint, datetime.now(timezone.utc).isoformat()))
                changed += 1
            if path:
                connection.commit()
            else:
                connection.execute('RELEASE failure_ignore')
        except Exception:
            if path:
                connection.rollback()
            else:
                connection.execute('ROLLBACK TO failure_ignore')
                connection.execute('RELEASE failure_ignore')
            raise
        finally:
            if path:
                connection.close()
        return {'changed': changed, 'skipped': len(unique) - changed}
