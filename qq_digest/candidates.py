from __future__ import annotations

import json
from datetime import datetime, timezone

from .archive import Archive
from .models import SummaryCandidate


class CandidateService:
    def __init__(self, archive: Archive):
        self.archive = archive

    def create(self, **kwargs) -> int:
        with self.archive.transaction():
            return self.create_in_transaction(**kwargs)

    def create_in_transaction(self, **kwargs) -> int:
        return self._create(kwargs, datetime.now(timezone.utc).isoformat())

    def _create(self, kwargs: dict, now: str) -> int:
        if not kwargs["message_ids"]:
            raise ValueError("候选必须关联至少一条消息")
        existing = self.archive.connection.execute(
            """
            SELECT candidate_id, status FROM candidates
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
            if existing["status"] == "pending":
                self.archive.connection.execute(
                    """
                    UPDATE candidates
                    SET content=?, reason=?, excerpt=?, updated_at=?
                    WHERE candidate_id=?
                    """,
                    (
                        kwargs.get("content", ""),
                        kwargs["reason"],
                        kwargs.get("excerpt", ""),
                        now,
                        existing["candidate_id"],
                    ),
                )
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
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def query(
        self,
        *,
        status: str = "pending",
        group_id: int | None = None,
        candidate_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str = "",
        limit: int = 200,
    ) -> list[SummaryCandidate]:
        if status not in {"pending", "later", "confirmed", "ignored", "all"}:
            raise ValueError("非法候选状态")
        if candidate_type not in {None, "", "resource", "experience"}:
            raise ValueError("非法候选类型")

        clauses: list[str] = []
        params: list[object] = []
        if status != "all":
            clauses.append("status=?")
            params.append(status)
        if group_id is not None:
            clauses.append("group_id=?")
            params.append(group_id)
        if candidate_type:
            clauses.append("candidate_type=?")
            params.append(candidate_type)
        if date_from:
            clauses.append("created_date>=?")
            params.append(date_from)
        if date_to:
            clauses.append("created_date<=?")
            params.append(date_to)
        if q.strip():
            pattern = f"%{q.strip()}%"
            clauses.append(
                "(title LIKE ? OR link LIKE ? OR content LIKE ? OR reason LIKE ? OR excerpt LIKE ?)"
            )
            params.extend([pattern] * 5)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self.archive.connection.execute(
            f"SELECT * FROM candidates{where} ORDER BY updated_at DESC, candidate_id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_candidate(row) for row in rows]

    def pending(self, group_id: int | None = None) -> list[SummaryCandidate]:
        return self.query(status="pending", group_id=group_id)

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
