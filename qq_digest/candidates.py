from __future__ import annotations

import json
from datetime import datetime, timezone

from .archive import Archive
from .models import SummaryCandidate


class CandidateService:
    def __init__(self, archive: Archive):
        self.archive = archive

    def create(self, **kwargs) -> int:
        now = datetime.now(timezone.utc).isoformat()
        if not kwargs["message_ids"]:
            raise ValueError("候选必须关联至少一条消息")
        with self.archive.transaction():
            existing = self.archive.connection.execute(
                """
                SELECT candidate_id FROM candidates
                WHERE group_id=? AND created_date=? AND candidate_type=? AND title=?
                  AND link=? AND message_ids=?
                """,
                (
                    kwargs["group_id"],
                    kwargs["created_date"],
                    kwargs["candidate_type"],
                    kwargs["title"],
                    kwargs.get("link", ""),
                    json.dumps(kwargs["message_ids"], ensure_ascii=False),
                ),
            ).fetchone()
            if existing is not None:
                return int(existing["candidate_id"])
            cursor = self.archive.connection.execute(
                """
                INSERT INTO candidates(
                    group_id, message_ids, created_date, candidate_type, title,
                    link, content, reason, excerpt, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    kwargs["group_id"],
                    json.dumps(kwargs["message_ids"], ensure_ascii=False),
                    kwargs["created_date"],
                    kwargs["candidate_type"],
                    kwargs["title"],
                    kwargs.get("link", ""),
                    kwargs.get("content", ""),
                    kwargs["reason"],
                    kwargs.get("excerpt", ""),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def _row_to_candidate(self, row) -> SummaryCandidate:
        return SummaryCandidate(
            candidate_id=row["candidate_id"],
            group_id=row["group_id"],
            message_ids=json.loads(row["message_ids"]),
            created_date=row["created_date"],
            candidate_type=row["candidate_type"],
            title=row["title"],
            link=row["link"],
            content=row["content"],
            reason=row["reason"],
            excerpt=row["excerpt"],
            status=row["status"],
            ignore_reason=row["ignore_reason"],
        )

    def pending(self, group_id: int | None = None) -> list[SummaryCandidate]:
        query = "SELECT * FROM candidates WHERE status='pending'"
        params: tuple = ()
        if group_id is not None:
            query += " AND group_id=?"
            params = (group_id,)
        query += " ORDER BY created_at DESC"
        return [self._row_to_candidate(row) for row in self.archive.connection.execute(query, params)]

    def get(self, candidate_id: int) -> SummaryCandidate:
        row = self.archive.connection.execute(
            "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"候选 {candidate_id} 不存在")
        return self._row_to_candidate(row)

    def update_status(self, candidate_id: int, status: str, ignore_reason: str = "") -> None:
        if status not in {"pending", "confirmed", "ignored", "later"}:
            raise ValueError("非法候选状态")
        now = datetime.now(timezone.utc).isoformat()
        with self.archive.transaction():
            cursor = self.archive.connection.execute(
                """
                UPDATE candidates SET status=?, ignore_reason=?, updated_at=?
                WHERE candidate_id=?
                """,
                (status, ignore_reason, now, candidate_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"候选 {candidate_id} 不存在")

    def confirm(self, candidate_id: int) -> None:
        self.update_status(candidate_id, "confirmed")

    def ignore(self, candidate_id: int, reason: str = "") -> None:
        self.update_status(candidate_id, "ignored", reason)
