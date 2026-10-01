"""Inspect local source IDs without importing messages or changing sync state."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, StrictBool

from .archive import Archive
from .collector.ntqq import NTQQCollector
from .config import Config


class InspectionSettings(BaseModel):
    enabled: StrictBool = True
    lookback_days: int = Field(default=7, ge=1, le=31, strict=True)
    interval_hours: int = Field(default=24, ge=1, le=168, strict=True)


class HistoryInspectionService:
    def __init__(self, archive: Archive, *, timezone_name: str = "Asia/Shanghai"):
        self.archive = archive
        self.timezone = ZoneInfo(timezone_name)

    def settings(self) -> dict:
        row = self.archive.connection.execute(
            "SELECT enabled,lookback_days,interval_hours FROM history_inspection_settings WHERE settings_id=1"
        ).fetchone()
        return {"enabled": bool(row["enabled"]), "lookback_days": row["lookback_days"],
                "interval_hours": row["interval_hours"]}

    def update_settings(self, settings: InspectionSettings) -> dict:
        with self.archive.transaction():
            self.archive.connection.execute(
                """UPDATE history_inspection_settings SET enabled=?,lookback_days=?,interval_hours=?
                   WHERE settings_id=1""", (int(settings.enabled), settings.lookback_days, settings.interval_hours)
            )
        return self.settings()

    def latest_run(self):
        row = self.archive.connection.execute(
            "SELECT * FROM jobs WHERE job_type='history_inspection' ORDER BY job_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def due(self, now: datetime) -> bool:
        settings = self.settings()
        if not settings["enabled"]:
            return False
        latest = self.latest_run()
        if not latest or latest["status"] == "running":
            return True
        try:
            finished = datetime.fromisoformat(latest["finished_at"])
        except (TypeError, ValueError):
            return True
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return now - finished >= timedelta(hours=settings["interval_hours"])

    def _present_ids(self, group_id: int, ids: list[str]) -> set[str]:
        present = set()
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            present.update(row["msg_id"] for row in self.archive.connection.execute(
                f"SELECT msg_id FROM messages WHERE group_id=? AND msg_id IN ({placeholders})",
                [group_id, *batch],
            ))
        return present

    def run(self, collector, *, now: datetime | None = None, prepare=None) -> dict:
        current = (now or datetime.now(self.timezone)).astimezone(self.timezone)
        days = self.settings()["lookback_days"]
        end = datetime.combine(current.date(), time.min, self.timezone)
        start = end - timedelta(days=days)
        checked_at = datetime.now(timezone.utc).isoformat()
        job_id = self.archive.start_job("history_inspection")
        outcomes, errors = {}, []
        missing_count = 0
        try:
            if prepare:
                prepare()
            for group in self.archive.enabled_groups():
                try:
                    # Exhaust the source before accepting results: a late read
                    # failure must never publish a partial scan as successful.
                    messages = list(collector.collect(group.group_id, start, end))
                    unique = {}
                    for message in messages:
                        if message.group_id != group.group_id:
                            raise ValueError("源库返回了其他群的消息，无法确认检查结果")
                        if start <= message.timestamp < end:
                            unique.setdefault(message.msg_id, message)
                    present = self._present_ids(group.group_id, list(unique))
                    counts = Counter(m.timestamp.astimezone(self.timezone).date().isoformat()
                                     for msg_id, m in unique.items() if msg_id not in present)
                    missing_days = [{"date": day, "count": counts[day]} for day in sorted(counts)]
                    with self.archive.transaction():
                        self.archive.connection.execute(
                            """INSERT INTO history_inspection_groups
                               (group_id,last_job_id,status,checked_at,last_success_at,start_date,end_date,source_count,missing_days,error)
                               VALUES (?,?,'success',?,?,?,?,?,?,'')
                               ON CONFLICT(group_id) DO UPDATE SET last_job_id=excluded.last_job_id,
                                 status='success',checked_at=excluded.checked_at,last_success_at=excluded.last_success_at,
                                 start_date=excluded.start_date,end_date=excluded.end_date,
                                 source_count=excluded.source_count,missing_days=excluded.missing_days,error=''
                               WHERE excluded.last_job_id>history_inspection_groups.last_job_id""",
                            (group.group_id, job_id, checked_at, checked_at, start.date().isoformat(),
                             (end.date()-timedelta(days=1)).isoformat(), len(unique), json.dumps(missing_days)),
                        )
                    missing_count += sum(counts.values())
                    outcomes[group.group_id] = "success"
                except Exception as exc:
                    error = " ".join(str(exc).split())[:300] or type(exc).__name__
                    errors.append(f"{group.name}: {error}")
                    outcomes[group.group_id] = "failed"
                    with self.archive.transaction():
                        self.archive.connection.execute(
                            """INSERT INTO history_inspection_groups(group_id,last_job_id,status,checked_at,error)
                               VALUES (?,?,'failed',?,?) ON CONFLICT(group_id) DO UPDATE SET
                               last_job_id=excluded.last_job_id,status='failed',checked_at=excluded.checked_at,error=excluded.error
                               WHERE excluded.last_job_id>history_inspection_groups.last_job_id""",
                            (group.group_id, job_id, checked_at, error),
                        )
            status = "partial_success" if errors and "success" in outcomes.values() else "failed" if errors else "success"
        except Exception as exc:
            errors.append(" ".join(str(exc).split())[:300] or type(exc).__name__)
            status = "failed"
        self.archive.finish_job(job_id, status, "\n".join(errors), group_outcomes=outcomes)
        return {"job_id": job_id, "status": status, "groups_checked": len(outcomes),
                "groups_failed": sum(value == "failed" for value in outcomes.values()),
                "missing_count": missing_count, "errors": errors,
                "start_date": start.date().isoformat(), "end_date": (end.date()-timedelta(days=1)).isoformat()}

    def snapshot(self, *, page: int = 1, page_size: int = 10) -> dict:
        latest = self.latest_run()
        total = self.archive.connection.execute("SELECT COUNT(*) FROM groups WHERE enabled=1").fetchone()[0]
        page = min(page, max(1, (total + page_size - 1) // page_size))
        rows = self.archive.connection.execute(
            """SELECT g.group_id,g.name,s.* FROM groups g LEFT JOIN history_inspection_groups s ON s.group_id=g.group_id
               WHERE g.enabled=1 ORDER BY CASE WHEN s.status='failed' THEN 0 WHEN s.missing_days<>'[]' THEN 1
                 WHEN s.status IS NULL THEN 2 ELSE 3 END,g.group_id LIMIT ? OFFSET ?""",
            (page_size, (page-1)*page_size),
        ).fetchall()
        groups = []
        for row in rows:
            item = dict(row)
            item["group_name"] = item.pop("name")
            item["missing_days"] = json.loads(item["missing_days"] or "[]")
            item["missing_count"] = sum(day["count"] for day in item["missing_days"])
            item["is_latest"] = bool(latest and item["last_job_id"] == latest["job_id"])
            item["status"] = item["status"] or "unchecked"
            item["label"] = ("无法检查" if item["status"] == "failed" else "尚未检查" if item["status"] == "unchecked"
                else "发现待补消息" if item["missing_count"] else "源库无可读取消息" if item["source_count"] == 0
                else "本范围未发现缺口")
            groups.append(item)
        return {"settings": self.settings(), "latest_run": latest, "groups": groups,
                "total": total, "page": page, "page_size": page_size}


def run_history_inspection(*, config: Config, refresh: bool = False, now=None, collector=None) -> dict:
    if not config.ntqq.enabled or not config.ntqq.db_dir:
        raise ValueError("请先启用 NTQQ 并配置源数据库目录")
    archive = Archive.open(config.archive_path)
    try:
        def prepare():
            if refresh:
                from .refresh import refresh_database
                result = refresh_database(qq_number=config.ntqq.qq_number, output_dir=config.ntqq.db_dir,
                                          snapshot_root=config.work_dir / "snapshots")
                if not result.success:
                    raise RuntimeError(f"数据库刷新失败：{result.message}")
        active = collector or NTQQCollector(db_dir=config.ntqq.db_dir,
            qq_number=config.ntqq.qq_number, timezone_name=config.ntqq.timezone)
        return HistoryInspectionService(archive, timezone_name=config.summary.timezone).run(active, now=now, prepare=prepare)
    finally:
        archive.close()
