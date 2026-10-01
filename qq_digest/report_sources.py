from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def extract_report_sources(payload: dict[str, Any]) -> list[dict[str, object]] | None:
    """Build display rows from a report's validated per-claim message references.

    Older reports have no evidence_version and must not gain guessed citations.
    """
    if payload.get("evidence_version") != 1:
        return None

    items: list[dict[str, object]] = []

    def add(section: str, text: object, message_ids: object) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        ids = list(dict.fromkeys(
            value for value in message_ids
            if isinstance(value, str) and value.strip()
        ))[:3] if isinstance(message_ids, list) else []
        items.append({
            "section": section,
            "text": text.strip(),
            "source_ids": ids,
            "status": "cited" if ids else "unverified",
        })

    add("今日概览", payload.get("overview"), payload.get("overview_message_ids"))
    for row in payload.get("main_topics", []):
        if isinstance(row, dict):
            title = row.get("topic", "")
            summary = row.get("summary", "")
            add("主要话题", f"{title}：{summary}" if summary else title, row.get("message_ids"))
    for row in payload.get("conclusions", []):
        add("重要结论", row.get("text") if isinstance(row, dict) else row,
            row.get("message_ids") if isinstance(row, dict) else [])
    for row in payload.get("resources", []):
        if isinstance(row, dict):
            title = row.get("title", "")
            description = row.get("description", "")
            url = row.get("url", "")
            text = f"{title}：{description}" if description else str(title)
            if url:
                text += f" · {url}"
            add("资源与链接", text, row.get("message_ids"))
    for row in payload.get("tasks", []):
        if isinstance(row, dict):
            text = f"{row.get('owner', '')}：{row.get('description', '')}"
            if row.get("deadline"):
                text += f"（{row['deadline']}）"
            add("任务或承诺", text, row.get("message_ids"))
    for row in payload.get("open_questions", []):
        add("未解决问题或争议", row.get("text") if isinstance(row, dict) else row,
            row.get("message_ids") if isinstance(row, dict) else [])
    return items


def load_verified_report_sources(
    connection: sqlite3.Connection, json_path: str | Path, *, group_id: int,
    start_date: str, end_date: str, timezone_name: str,
) -> list[dict[str, object]] | None:
    """Discard references that are missing, from another group, or outside the report."""
    try:
        payload = json_path if isinstance(json_path, dict) else json.loads(Path(json_path).read_text(encoding="utf-8"))
        items = extract_report_sources(payload) if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError):
        return None
    if items is None:
        return None
    ids = list(dict.fromkeys(
        source_id for item in items for source_id in item["source_ids"]
    ))
    if not ids:
        return items
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT msg_id, timestamp FROM messages WHERE group_id=? AND msg_id IN ({placeholders})",
        (group_id, *ids),
    ).fetchall()
    local_timezone = ZoneInfo(timezone_name)
    valid_ids: set[str] = set()
    for row in rows:
        try:
            timestamp = datetime.fromisoformat(row["timestamp"])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            local_date = timestamp.astimezone(local_timezone).date().isoformat()
        except (TypeError, ValueError):
            continue
        if start_date <= local_date <= end_date:
            valid_ids.add(row["msg_id"])
    for item in items:
        item["source_ids"] = [source_id for source_id in item["source_ids"] if source_id in valid_ids]
        item["status"] = "cited" if item["source_ids"] else "unverified"
    return items
