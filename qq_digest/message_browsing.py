"""Read paginated messages from the local archive without running collection."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .archive import Archive
from .search import _snippet


def _date_value(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期需为 YYYY-MM-DD 格式的有效日期") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期需为 YYYY-MM-DD 格式的有效日期")
    return parsed


def browse_messages(
    archive: Archive, *, group_id: int | None = None,
    date_from: str | None = None, date_to: str | None = None,
    page: int = 1, page_size: int = 20, timezone_name: str = "Asia/Shanghai",
) -> dict:
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("页码需至少为 1，每页数量需为 1 到 100")
    offset = (page - 1) * page_size
    if offset > 2**63 - 1 or (group_id is not None and not -(2**63) <= group_id < 2**63):
        raise ValueError("分页或群号超出有效范围")
    zone = ZoneInfo(timezone_name)
    today = datetime.now(zone).date()
    first = _date_value(date_from)
    last = _date_value(date_to)
    try:
        if first is None:
            last = last or today
            first = last - timedelta(days=6)
        if last is None:
            last = max(today, first)
        if first > last:
            raise ValueError("开始日期不能晚于结束日期")
        start = datetime.combine(first, time.min, zone).astimezone(timezone.utc).isoformat()
        end = datetime.combine(last + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat()
    except OverflowError as exc:
        raise ValueError("日期超出支持的查询范围") from exc

    clauses = ["m.timestamp>=?", "m.timestamp<?"]
    params: list[object] = [start, end]
    if group_id is not None:
        clauses.append("m.group_id=?")
        params.append(group_id)
    source = " FROM messages m JOIN groups g ON g.group_id=m.group_id"
    where = " WHERE " + " AND ".join(clauses)
    connection = archive.connection
    total = connection.execute("SELECT COUNT(*)" + source + where, params).fetchone()[0]
    rows = connection.execute(
        "SELECT m.msg_id, m.group_id, m.sender_qq, m.timestamp, m.message_type, "
        "m.text, g.name AS group_name" + source + where
        + " ORDER BY m.timestamp DESC, m.group_id ASC, m.msg_id ASC LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    ).fetchall()
    results = []
    for row in rows:
        local_time = datetime.fromisoformat(row["timestamp"]).astimezone(zone)
        results.append({
            "kind": "message", "id": row["msg_id"], "group_id": row["group_id"],
            "group_name": row["group_name"], "date": local_time.date().isoformat(),
            "sort_at": row["timestamp"],
            "title": f"{row['group_name']} · {row['sender_qq'] or '未知发送者'}",
            "snippet": _snippet(row["text"], ""), "text": row["text"], "url": "",
            "timestamp": row["timestamp"], "sender_qq": row["sender_qq"],
            "message_type": row["message_type"],
        })
    return {"results": results, "total": total, "page": page, "page_size": page_size}
