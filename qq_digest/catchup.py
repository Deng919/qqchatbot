"""Cross-group catch-up feed from existing daily reports."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .report_selection import selected_report_groups
from .report_sources import extract_report_sources, load_verified_report_sources


_ITEM_KEY = re.compile(r"[0-9a-f]{64}\Z")
_SECTIONS = {"主要话题", "重要结论", "资源与链接", "任务或承诺", "未解决问题或争议"}


def report_item_key(kind, report_id, section, text, source_ids, occurrence):
    identity_id = report_id if kind == 'daily' else f'range:{report_id}'
    material = json.dumps([identity_id, section, text, source_ids, occurrence],
                          ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


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
                   page_size: int = 30, date_from: str | None = None,
                   date_to: str | None = None, group_id: int | None = None,
                   read_filter: str = "all", include_ranges: bool = False,
                   group_ids: list[int] | None = None) -> dict:
        if scope not in {"since", "today", "week", "custom"}:
            raise ValueError("补看范围无效")
        if read_filter not in {"all", "unread", "new"}:
            raise ValueError("阅读筛选无效")
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("页码或每页数量无效")
        # Legacy date scopes ignored since, and the legacy since feed treated
        # an empty value as the seven-day fallback. Custom selection is strict.
        if scope != "custom" and since == "":
            since = None
        try:
            cutoff = self._cutoff(since) if since is not None else None
        except ValueError:
            if scope not in {"today", "week"}:
                raise
            cutoff = None
        current = self._now(now)
        today = current.astimezone(self.timezone).date()
        conditions: list[str] = []
        params: list[object] = []
        if scope == "custom":
            first = self._date(date_from) if date_from is not None else today
            last = self._date(date_to) if date_to is not None else today
            if first > last:
                raise ValueError("开始日期不能晚于结束日期")
            conditions.append("r.report_date BETWEEN ? AND ?")
            params.extend([first.isoformat(), last.isoformat()])
            if include_ranges:
                conditions.append('r.start_date>=?')
                params.append(first.isoformat())
        elif scope == "today":
            conditions.append("r.report_date=?")
            params.append(today.isoformat())
            if include_ranges:
                conditions.append('r.start_date=?')
                params.append(today.isoformat())
        elif scope == "week" or cutoff is None:
            conditions.append("r.report_date BETWEEN ? AND ?")
            params.extend([(today - timedelta(days=6)).isoformat(), today.isoformat()])
            if include_ranges:
                conditions.append('r.start_date>=?')
                params.append((today-timedelta(days=6)).isoformat())
        else:
            # Compare timestamps as instants below; persisted ISO offsets may differ.
            conditions.append("1=1")
        selection = selected_report_groups(group_id,group_ids)
        if selection:
            conditions.append('r.group_id IN ('+','.join('?' for _ in selection)+')')
            params.extend(selection)
        report_rows = "(SELECT 'daily' AS report_kind,report_id,group_id,report_date,report_date AS start_date,json_path,created_at FROM reports"
        if include_ranges:
            report_rows += " UNION ALL SELECT 'range',manual_report_id,group_id,end_date,start_date,json_path,updated_at FROM manual_reports"
        report_rows += ')'
        rows = self.archive.connection.execute(
            "SELECT r.report_id, r.report_kind, r.start_date, r.group_id, r.report_date, r.json_path, "
            "r.created_at, g.name AS group_name "
            f"FROM {report_rows} r JOIN groups g ON g.group_id=r.group_id "
            "WHERE " + " AND ".join(conditions) +
            " ORDER BY r.report_date DESC, r.created_at DESC, r.report_id DESC",
            params,
        ).fetchall()
        items: list[dict] = []
        skipped_reports = 0
        for report in rows:
            is_new = cutoff is None
            if cutoff is not None:
                try:
                    is_new = self._cutoff(report["created_at"]) > cutoff
                except (ValueError, TypeError):
                    is_new = False
            if scope == "since" and cutoff is not None and not is_new:
                continue
            try:
                payload = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("report payload must be an object")
                if payload.get("evidence_version") == 1:
                    evidence = load_verified_report_sources(
                        self.archive.connection, report["json_path"],
                        group_id=report["group_id"],
                        start_date=report["start_date"],
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
                items.append({
                    "key": report_item_key(report['report_kind'], report['report_id'],
                                           item['section'], item['text'], source_ids, occurrence),
                    "group_id": report["group_id"],
                    "group_name": report["group_name"],
                    "report_date": report["report_date"],
                    "report_kind": report['report_kind'],
                    "date_label": report['report_date'] if report['start_date']==report['report_date'] else f"{report['start_date']} 至 {report['report_date']}",
                    "section": item["section"],
                    "text": item["text"],
                    "source_ids": source_ids,
                    "status": item["status"],
                    "report_id": report["report_id"],
                    "report_url": f"/reports?kind={report['report_kind']}&id={report['report_id']}",
                    "new": is_new,
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
        unread = sum(not item["read"] for item in items)
        new_count = sum(item["new"] for item in items)
        if read_filter == "unread":
            items = [item for item in items if not item["read"]]
        elif read_filter == "new":
            items = [item for item in items if item["new"]]
        total = len(items)
        return {
            "items": items[(page - 1) * page_size:page * page_size],
            "total": total,
            "unread": unread,
            "new_count": new_count,
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
    def _date(value: str) -> date:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
            return parsed
        except (TypeError, ValueError) as exc:
            raise ValueError("日期必须为 YYYY-MM-DD 自然日") from exc

    @staticmethod
    def _cutoff(value: str) -> datetime:
        try:
            cutoff = datetime.fromisoformat(value)
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError
            return cutoff.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ValueError("上次查看时间无效，必须包含时区") from exc

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return result.astimezone(timezone.utc)
