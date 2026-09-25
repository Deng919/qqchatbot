"""Bounded original-message context for candidate review."""

from __future__ import annotations

from .archive import Archive
from .models import SummaryCandidate


def candidate_source_context(archive: Archive, candidate: SummaryCandidate) -> dict:
    ids = list(dict.fromkeys(candidate.message_ids))
    cited_rows = {}
    for msg_id in ids:
        row = archive.connection.execute(
            "SELECT * FROM messages WHERE group_id=? AND msg_id=?",
            (candidate.group_id, msg_id),
        ).fetchone()
        if row is not None:
            cited_rows[msg_id] = row

    all_rows = dict(cited_rows)
    for source in cited_rows.values():
        before = archive.connection.execute(
            """SELECT * FROM messages WHERE group_id=?
               AND (timestamp < ? OR (timestamp = ? AND msg_id < ?))
               ORDER BY timestamp DESC, msg_id DESC LIMIT 2""",
            (candidate.group_id, source["timestamp"], source["timestamp"], source["msg_id"]),
        ).fetchall()
        after = archive.connection.execute(
            """SELECT * FROM messages WHERE group_id=?
               AND (timestamp > ? OR (timestamp = ? AND msg_id > ?))
               ORDER BY timestamp, msg_id LIMIT 2""",
            (candidate.group_id, source["timestamp"], source["timestamp"], source["msg_id"]),
        ).fetchall()
        for row in (*before, *after):
            all_rows[row["msg_id"]] = row

    order = lambda row: (row["timestamp"], row["msg_id"])
    chosen = {row["msg_id"]: row for row in sorted(cited_rows.values(), key=order)[:40]}
    for row in sorted(all_rows.values(), key=order):
        if len(chosen) >= 40:
            break
        chosen[row["msg_id"]] = row
    selected = sorted(chosen.values(), key=order)
    return {
        "source_context": [
            {
                "msg_id": row["msg_id"],
                "timestamp": row["timestamp"],
                "sender_qq": row["sender_qq"],
                "text": row["text"][:2000],
                "is_cited": row["msg_id"] in cited_rows,
                "text_truncated": len(row["text"]) > 2000,
            }
            for row in selected
        ],
        "source_context_truncated": len(all_rows) > len(chosen),
        "missing_source_count": len(ids) - len(cited_rows),
    }
