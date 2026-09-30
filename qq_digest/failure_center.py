"""Read a bounded, human-readable view of persisted operational failures."""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlencode

from .archive import Archive


class FailureCenterService:
    def __init__(self, archive: Archive):
        self.archive = archive

    @staticmethod
    def _text(value: object, limit: int = 500) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _group_failures(error: str) -> list[dict]:
        try:
            value = json.loads(error)
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value[:100] if isinstance(item, dict)]

    def _failure_details(self, job) -> list[dict]:
        failures = self._group_failures(job["error"])
        if failures:
            return failures
        if job["job_type"] == "message_sync":
            rows = self.archive.connection.execute(
                """SELECT group_id FROM job_group_results
                   WHERE job_id=? AND status='failed' ORDER BY group_id""",
                (job["job_id"],),
            ).fetchall()
            if rows:
                return [{"group_id": row["group_id"], "stage": "collection",
                         "error": job["error"]} for row in rows]
        return [{"error": job["error"] or "任务未完成"}]

    def _item_recovered(self, job, group_id: int | None) -> bool:
        connection = self.archive.connection
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

    def list_items(self) -> dict:
        connection = self.archive.connection
        groups = {
            row["group_id"]: row["name"]
            for row in connection.execute("SELECT group_id,name FROM groups")
        }
        jobs = connection.execute(
            """SELECT * FROM jobs WHERE status IN ('failed','partial_success')
               ORDER BY job_id DESC LIMIT 100"""
        ).fetchall()

        items: list[dict] = []
        for job in jobs:
            failures = self._failure_details(job)
            retry = connection.execute(
                """SELECT job_id,status FROM jobs WHERE retry_of_job_id=?
                   ORDER BY job_id DESC LIMIT 1""",
                (job["job_id"],),
            ).fetchone()
            for index, failure in enumerate(failures):
                raw_gid = failure.get("group_id")
                group_id = raw_gid if type(raw_gid) is int else None
                recovered = self._item_recovered(job, group_id)
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
                    "stage": self._text(failure.get("stage") or kind, 50),
                    "error": self._text(failure.get("error") or job["error"]),
                    "at": job["finished_at"] or job["started_at"] or "",
                    "status": "recovered" if recovered else "active",
                    "action": "retry_job" if (
                        kind == "message_sync" or
                        (kind == "daily_digest" and bool(job["target_date"]))
                    ) and not recovered else "",
                    "next_step": (
                        "该群的后续任务已成功" if recovered and group_id is not None else
                        "较新的同日期任务已成功" if recovered else
                        "旧任务缺少报告日期，请在总览重新运行" if kind == "daily_digest" and not job["target_date"] else
                        "检查 AI 设置与采集状态后重试" if kind == "daily_digest" else
                        "检查本地 QQ 数据库后重试导入" if kind == "message_sync" else
                        "查看任务日志并修复原因"
                    ),
                    "retry_job_id": retry["job_id"] if retry else None,
                    "retry_status": retry["status"] if retry else "",
                })

        sync_rows = connection.execute(
            """SELECT s.group_id,s.status,s.error,s.updated_at,g.name
               FROM sync_state s LEFT JOIN groups g ON g.group_id=s.group_id
               WHERE s.error<>'' AND s.status NOT IN ('active','manual_repair_completed')
               ORDER BY s.updated_at DESC LIMIT 100"""
        ).fetchall()
        today = date.today()
        for row in sync_rows:
            group_id = row["group_id"]
            items.append({
                "id": f"sync:{group_id}", "kind": "group_sync",
                "job_id": None, "group_id": group_id,
                "group_name": row["name"] or str(group_id),
                "target_date": "", "stage": "collection",
                "error": self._text(row["error"]), "at": row["updated_at"],
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
                ORDER BY send_id DESC LIMIT 100"""
        ).fetchall()
        recovered_sends = connection.execute(
            f"""SELECT {send_columns} FROM send_log
                WHERE status='success' AND (retry_count>0 OR manual_retry_count>0)
                ORDER BY send_id DESC LIMIT 100"""
        ).fetchall()
        sends = [*active_sends, *recovered_sends[:max(0, 100 - len(active_sends))]]
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
                "target_date": self._text(payload.get("report_date"), 20),
                "stage": "qq_bot", "error": self._text(row["error"]),
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

        items.sort(key=lambda item: item["at"], reverse=True)
        items.sort(key=lambda item: {"active": 0, "pending": 1, "recovered": 2}[item["status"]])
        return {
            "items": items[:400],
            "counts": {
                "active": sum(item["status"] == "active" for item in items),
                "pending": sum(item["status"] == "pending" for item in items),
                "recovered": sum(item["status"] == "recovered" for item in items),
            },
        }
