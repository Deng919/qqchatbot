"""Cross-group catch-up feed from existing daily reports."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .report_sources import extract_report_sources, load_verified_report_sources


_ITEM_KEY = re.compile(r"[0-9a-f]{64}\Z")
_SECTIONS = {"主要话题", "重要结论", "资源与链接", "任务或承诺", "未解决问题或争议"}


class CatchupService:
    def __init__(self, archive: Archive, *, timezone_name: str):
        self.archive = archive
        self.timezone = ZoneInfo(timezone_name)
        self.timezone_name = timezone_name

    def visit(self, *, now: datetime | None = None) -> dict[str, str | None]:
        current = self._now(now).isoformat()
        with self.archive.transaction():
            row = self.archive.connection.execute(
                "SELECT last_viewed_at FROM catchup_state WHERE state_id=1"
            ).fetchone()
            self.archive.connection.execute(
                "INSERT INTO catchup_state(state_id,last_viewed_at) VALUES (1,?) "
                "ON CONFLICT(state_id) DO UPDATE SET last_viewed_at=excluded.last_viewed_at",
                (current,),
            )
        return {"previous_viewed_at": row["last_viewed_at"] if row else None,
                "viewed_at": current}

    def list_items(self, scope: str, *, since: str | None = None,
                   now: datetime | None = None, page: int = 1,
                   page_size: int = 30) -> dict:
        if scope not in {"since", "today", "week"}:
            raise ValueError("补看范围无效")
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("页码或每页数量无效")
        current = self._now(now)
        today = current.astimezone(self.timezone).date()
        conditions: list[str] = []
        params: list[object] = []
        if scope == "today":
            conditions.append("r.report_date=?")
            params.append(today.isoformat())
        elif scope == "week" or not since:
            conditions.append("r.report_date BETWEEN ? AND ?")
            params.extend([(today - timedelta(days=6)).isoformat(), today.isoformat()])
        else:
            try:
                cutoff = datetime.fromisoformat(since)
            except ValueError as exc:
                raise ValueError("上次查看时间无效") from exc
            if cutoff.tzinfo is None:
                raise ValueError("上次查看时间无效")
            conditions.append("r.created_at>?")
            params.append(cutoff.astimezone(timezone.utc).isoformat())
        rows = self.archive.connection.execute(
            "SELECT r.report_id, r.group_id, r.report_date, r.json_path, "
            "r.created_at, g.name AS group_name "
            "FROM reports r JOIN groups g ON g.group_id=r.group_id "
            "WHERE " + " AND ".join(conditions) +
            " ORDER BY r.report_date DESC, r.created_at DESC, r.report_id DESC",
            params,
        ).fetchall()
        items: list[dict] = []
        skipped_reports = 0
        for report in rows:
            try:
                payload = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("report payload must be an object")
                if payload.get("evidence_version") == 1:
                    evidence = load_verified_report_sources(
                        self.archive.connection, report["json_path"],
                        group_id=report["group_id"],
                        start_date=report["report_date"],
                        end_date=report["report_date"],
                        timezone_name=self.timezone_name,
                    )
                    if evidence is None:
                        raise ValueError("report evidence unavailable")
                else:
                    evidence = extract_report_sources({**payload, "evidence_version": 1})
                    for entry in evidence or []:
                        entry["source_ids"] = []
                        entry["status"] = "legacy"
            except (OSError, UnicodeError, ValueError, TypeError, KeyError):
                skipped_reports += 1
                continue
            selected = [item for item in evidence or [] if item["section"] in _SECTIONS]
            if not selected:
                selected = [item for item in evidence or [] if item["section"] == "今日概览"]
            duplicates: dict[str, int] = {}
            for item in selected:
                source_ids = list(item["source_ids"])
                identity = json.dumps(
                    [item["section"], item["text"], source_ids],
                    ensure_ascii=False, separators=(",", ":"),
                )
                occurrence = duplicates.get(identity, 0)
                duplicates[identity] = occurrence + 1
                key_material = json.dumps(
                    [report["report_id"], item["section"], item["text"], source_ids, occurrence],
                    ensure_ascii=False, separators=(",", ":"),
                )
                items.append({
                    "key": hashlib.sha256(key_material.encode("utf-8")).hexdigest(),
                    "group_id": report["group_id"],
                    "group_name": report["group_name"],
                    "report_date": report["report_date"],
                    "section": item["section"],
                    "text": item["text"],
                    "source_ids": source_ids,
                    "status": item["status"],
                    "report_id": report["report_id"],
                    "report_url": f"/reports?kind=daily&id={report['report_id']}",
                })
        keys = [item["key"] for item in items]
        read_keys: set[str] = set()
        for start in range(0, len(keys), 500):
            batch = keys[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            read_keys.update(row["item_key"] for row in self.archive.connection.execute(
                f"SELECT item_key FROM catchup_reads WHERE item_key IN ({placeholders})",
                batch,
            ).fetchall())
        for item in items:
            item["read"] = item["key"] in read_keys
        total = len(items)
        return {
            "items": items[(page - 1) * page_size:page * page_size],
            "total": total,
            "unread": total - len(read_keys),
            "page": page,
            "page_size": page_size,
            "skipped_reports": skipped_reports,
        }

    def set_read(self, item_key: str, read: bool, *, now: datetime | None = None) -> None:
        if not _ITEM_KEY.fullmatch(item_key):
            raise ValueError("条目编号无效")
        with self.archive.transaction():
            if read:
                self.archive.connection.execute(
                    "INSERT INTO catchup_reads(item_key,read_at) VALUES (?,?) "
                    "ON CONFLICT(item_key) DO UPDATE SET read_at=excluded.read_at",
                    (item_key, self._now(now).isoformat()),
                )
            else:
                self.archive.connection.execute(
                    "DELETE FROM catchup_reads WHERE item_key=?", (item_key,)
                )

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return result.astimezone(timezone.utc)
