"""Local search across archived messages, report files, and reviewed knowledge."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .message_search import MESSAGE_TYPES, search_tokens
from .sender_identity import sender_fields


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, query: str, *, length: int = 210) -> str:
    compact = " ".join(text.split())
    index = compact.casefold().find(query.casefold())
    if index < 0:
        return compact[:length] + ("…" if len(compact) > length else "")
    start = max(0, index - 65)
    end = min(len(compact), start + length)
    return ("…" if start else "") + compact[start:end] + ("…" if end < len(compact) else "")


def _message_bounds(date_from: date | None, date_to: date | None, zone: ZoneInfo):
    start = datetime.combine(date_from, time.min, zone).astimezone(timezone.utc).isoformat() if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat() if date_to else None
    return start, end


def _result_order(item: dict) -> tuple:
    identity = str(item['id']) if item['kind'] == 'message' else int(item['id'])
    return (item['sort_at'], item['kind'], identity, item['group_id'],
            item.get('report_kind', ''))


def search_archive(
    archive: Archive, *, query: str, kind: str = "all", group_id: int | None = None,
    date_from: date | None = None, date_to: date | None = None,
    page: int = 1, page_size: int = 20, timezone_name: str = "Asia/Shanghai",
    sender_qq: int | None = None, message_type: str | None = None,
) -> dict:
    query = query.strip()
    if len(query) < 2 or len(query) > 100:
        raise ValueError("搜索词需为 2 到 100 个字符")
    if kind not in {"all", "message", "report", "knowledge"}:
        raise ValueError("非法搜索类型")
    if page < 1 or not 1 <= page_size <= 100 or page * page_size > 2**63 - 1:
        raise ValueError("分页超出有效范围")
    if sender_qq is not None and not 1 <= sender_qq < 2**63:
        raise ValueError("发送人 QQ 号超出有效范围")
    if group_id is not None and not -(2**63) <= group_id < 2**63:
        raise ValueError("群号超出有效范围")
    if message_type is not None and message_type not in MESSAGE_TYPES:
        raise ValueError("非法消息类型")
    if kind != 'message' and (sender_qq is not None or message_type is not None):
        raise ValueError("发送人与消息类型筛选仅用于聊天消息")
    if date_from and date_to and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期")
    zone = ZoneInfo(timezone_name)
    pattern = _like_pattern(query)
    fetch_limit = page * page_size
    results: list[dict] = []
    total = 0
    connection = archive.connection

    if kind in {"all", "message"}:
        clauses = ["m.text LIKE ? ESCAPE '\\'"]
        params: list[object] = [pattern]
        if archive.message_search_indexed and '\x00' not in query:
            clauses.append('m.rowid IN (SELECT rowid FROM message_search_fts WHERE message_search_fts MATCH ?)')
            params.append(search_tokens(query))
        if sender_qq is not None:
            clauses.append('m.sender_qq=?')
            params.append(sender_qq)
        if message_type is not None:
            clauses.append('m.message_type=?')
            params.append(message_type)
        if group_id is not None:
            clauses.append("m.group_id=?")
            params.append(group_id)
        start, end = _message_bounds(date_from, date_to, zone)
        if start:
            clauses.append("m.timestamp>=?")
            params.append(start)
        if end:
            clauses.append("m.timestamp<?")
            params.append(end)
        where = " WHERE " + " AND ".join(clauses)
        source = " FROM messages m JOIN groups g ON g.group_id=m.group_id"
        total += connection.execute("SELECT COUNT(*)" + source + where, params).fetchone()[0]
        rows = connection.execute(
            "SELECT m.msg_id, m.group_id, m.sender_qq, m.content_json, m.timestamp, m.text, m.message_type, g.name AS group_name"
            + source + where + " ORDER BY m.timestamp DESC, m.msg_id DESC, m.group_id DESC LIMIT ?",
            [*params, fetch_limit],
        ).fetchall()
        for row in rows:
            local_time = datetime.fromisoformat(row["timestamp"]).astimezone(zone)
            results.append({
                "kind": "message", "id": row["msg_id"], "group_id": row["group_id"],
                "group_name": row["group_name"], "date": local_time.date().isoformat(),
                "sort_at": row["timestamp"],
                "title": f"{row['group_name']} · {sender_fields(row).get('sender_label') or row['sender_qq'] or '未知发送者'}",
                "snippet": _snippet(row["text"], query), "text": row["text"],
                "timestamp": row["timestamp"], "sender_qq": row["sender_qq"],
                "message_type": row["message_type"],
                **sender_fields(row),
                "url": "",
            })

    if kind in {"all", "knowledge"}:
        clauses = ["c.status='confirmed'", "EXISTS (SELECT 1 FROM knowledge_items k WHERE k.candidate_id=c.candidate_id)", "(c.title LIKE ? ESCAPE '\\' OR c.content LIKE ? ESCAPE '\\' OR c.reason LIKE ? ESCAPE '\\' OR c.excerpt LIKE ? ESCAPE '\\' OR c.link LIKE ? ESCAPE '\\')"]
        params = [pattern] * 5
        if group_id is not None:
            clauses.append("c.group_id=?")
            params.append(group_id)
        if date_from:
            clauses.append("c.created_date>=?")
            params.append(date_from.isoformat())
        if date_to:
            clauses.append("c.created_date<=?")
            params.append(date_to.isoformat())
        where = " WHERE " + " AND ".join(clauses)
        source = " FROM candidates c JOIN groups g ON g.group_id=c.group_id"
        total += connection.execute("SELECT COUNT(*)" + source + where, params).fetchone()[0]
        rows = connection.execute(
            "SELECT c.*, g.name AS group_name" + source + where
            + " ORDER BY c.created_date DESC, c.candidate_id DESC LIMIT ?",
            [*params, fetch_limit],
        ).fetchall()
        for row in rows:
            body = " ".join([row["title"], row["content"], row["reason"], row["excerpt"]])
            results.append({
                "kind": "knowledge", "id": row["candidate_id"],
                "group_id": row["group_id"], "group_name": row["group_name"],
                "date": row["created_date"], "sort_at": row["created_date"] + "T00:00:00+00:00",
                "title": row["title"], "snippet": _snippet(body, query), "text": body,
                "url": f"/knowledge?item_id={row['candidate_id']}",
            })

    if kind in {"all", "report"}:
        report_rows = connection.execute(
            """SELECT 'daily' AS kind, r.report_id AS id, r.group_id, g.name AS group_name,
                      r.report_date AS start_date, r.report_date AS end_date,
                      r.markdown_path, r.created_at AS sort_at
               FROM reports r JOIN groups g ON g.group_id=r.group_id
               UNION ALL
               SELECT 'range', r.manual_report_id, r.group_id, g.name,
                      r.start_date, r.end_date, r.markdown_path, r.updated_at
               FROM manual_reports r JOIN groups g ON g.group_id=r.group_id
               ORDER BY sort_at DESC"""
        ).fetchall()
        matched_reports = []
        for row in report_rows:
            if group_id is not None and row["group_id"] != group_id:
                continue
            if date_from and row["end_date"] < date_from.isoformat():
                continue
            if date_to and row["start_date"] > date_to.isoformat():
                continue
            try:
                body = Path(row["markdown_path"]).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if query.casefold() not in body.casefold():
                continue
            matched_reports.append({
                "kind": "report", "id": row["id"], "report_kind": row["kind"],
                "group_id": row["group_id"], "group_name": row["group_name"],
                "date": row["end_date"], "sort_at": row["sort_at"],
                "title": f"{row['group_name']} · {row['start_date']}" + (f" 至 {row['end_date']}" if row["start_date"] != row["end_date"] else ""),
                "snippet": _snippet(body, query), "text": "",
                "url": f"/reports?kind={row['kind']}&id={row['id']}",
            })
        total += len(matched_reports)
        matched_reports.sort(key=_result_order, reverse=True)
        results.extend(matched_reports[:fetch_limit])

    results.sort(key=_result_order, reverse=True)
    start_index = (page - 1) * page_size
    for item in results:
        item.pop("sort_at")
    return {"results": results[start_index:start_index + page_size], "total": total,
            "page": page, "page_size": page_size}
