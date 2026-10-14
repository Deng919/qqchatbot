from __future__ import annotations

import json
from datetime import datetime, timezone

from .archive import Archive
from .group_selection import normalize_group_selection
from .models import SummaryCandidate


class CandidateConflictError(ValueError):
    """The stored candidate changed after the editor opened its draft."""


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
        identity = [
            kwargs['group_id'], kwargs['created_date'], kwargs['candidate_type'],
            kwargs['title'], kwargs.get('link', ''), kwargs['message_ids'],
        ]
        manual=kwargs.get('human_edited') is True
        generation_key = json.dumps(['manual',*identity] if manual else identity,ensure_ascii=False)
        existing = self.archive.connection.execute(
            """
            SELECT candidate_id, status, human_edited FROM candidates
            WHERE generation_key=? ORDER BY candidate_id LIMIT 1
            """,
            (generation_key,),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "pending" and not existing['human_edited']:
                self.archive.connection.execute(
                    """
                    UPDATE candidates
                    SET content=?, reason=?, excerpt=?, updated_at=?, revision=revision+1
                    WHERE candidate_id=? AND human_edited=0 AND status='pending'
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
                link, content, reason, excerpt, status, created_at, updated_at, generation_key,human_edited
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?,?)
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
                generation_key,
                int(manual),
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
            revision=row['revision'], human_edited=bool(row['human_edited']),
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
        group_ids: list[int] | tuple[int, ...] | None = None,
    ) -> list[SummaryCandidate]:
        where, params = self._filters(status=status, group_id=group_id, group_ids=group_ids,
            candidate_type=candidate_type, date_from=date_from, date_to=date_to, q=q)
        rows = self.archive.connection.execute(
            f"SELECT * FROM candidates{where} ORDER BY updated_at DESC, candidate_id DESC LIMIT ?",
            [*params, max(1, min(limit, 500))],
        ).fetchall()
        return [self._row_to_candidate(row) for row in rows]

    @staticmethod
    def _filters(*, status='pending', group_id=None, group_ids=None,
                 candidate_type=None, date_from=None, date_to=None, q=''):
        if status not in {"pending", "later", "confirmed", "ignored", "all"}:
            raise ValueError("非法候选状态")
        if candidate_type not in {None, "", "resource", "experience"}:
            raise ValueError("非法候选类型")

        selected = normalize_group_selection(group_id, group_ids)
        clauses: list[str] = []
        params: list[object] = []
        if status != "all":
            clauses.append("status=?")
            params.append(status)
        if selected:
            clauses.append('group_id IN (' + ','.join('?' for _ in selected) + ')')
            params.extend(selected)
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
        return where, params

    def count(self, **filters) -> int:
        where, params = self._filters(**filters)
        return self.archive.connection.execute(f'SELECT COUNT(*) FROM candidates{where}', params).fetchone()[0]

    def query_page(self, *, page=1, page_size=20, **filters) -> dict:
        if type(page) is not int or not 1 <= page <= 2**31 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('候选分页参数无效')
        where, params = self._filters(**filters)
        total = self.archive.connection.execute(f'SELECT COUNT(*) FROM candidates{where}', params).fetchone()[0]
        rows = self.archive.connection.execute(
            f'SELECT * FROM candidates{where} ORDER BY updated_at DESC, candidate_id DESC LIMIT ? OFFSET ?',
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {'items': [self._row_to_candidate(row) for row in rows], 'total': total,
                'page': page, 'page_size': page_size}

    def pending(self, group_id: int | None = None, *, group_ids=None) -> list[SummaryCandidate]:
        return self.query(status="pending", group_id=group_id, group_ids=group_ids)

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
                UPDATE candidates SET status=?, ignore_reason=?, updated_at=?, revision=revision+1
                WHERE candidate_id=?
                """,
                (status, ignore_reason, now, candidate_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"候选 {candidate_id} 不存在")

    def confirm(self, candidate_id: int) -> None:
        self.update_status(candidate_id, "confirmed")

    def update_details(
        self, candidate_id: int, *, candidate_type: str, title: str,
        link: str, content: str, reason: str, excerpt: str,
        expected_revision: int | None = None,
    ) -> SummaryCandidate:
        if candidate_type not in {"resource", "experience"}:
            raise ValueError("非法候选类型")
        if expected_revision is not None and (type(expected_revision) is not int or not 0<=expected_revision<2**63):
            raise ValueError('候选版本无效')
        with self.archive.transaction():
            cursor = self.archive.connection.execute(
                """UPDATE candidates
                   SET candidate_type=?, title=?, link=?, content=?, reason=?, excerpt=?,
                       updated_at=?, human_edited=1, revision=revision+1
                   WHERE candidate_id=? AND status IN ('pending', 'later')
                     AND (? IS NULL OR revision=?)""",
                (
                    candidate_type, title.strip(), link.strip(), content.strip(),
                    reason.strip(), excerpt.strip(), datetime.now(timezone.utc).isoformat(),
                    candidate_id,
                    expected_revision, expected_revision,
                ),
            )
            if not cursor.rowcount:
                current = self.get(candidate_id)
                if expected_revision is not None and current.revision != expected_revision:
                    raise CandidateConflictError('候选已被修改，请重新读取后核对草稿')
                raise ValueError("只能修改待审核或稍后处理的候选")
        return self.get(candidate_id)

    def ignore(self, candidate_id: int, reason: str = "") -> None:
        self.update_status(candidate_id, "ignored", reason)
