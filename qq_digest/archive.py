from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import GroupConfig, NormalizedMessage


@dataclass(frozen=True)
class IngestResult:
    inserted: int
    skipped: int


class Archive:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @classmethod
    def open(cls, path: Path) -> "Archive":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        return cls(connection)

    @contextmanager
    def transaction(self):
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate(self) -> None:
        with self.transaction():
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    daily_summary INTEGER NOT NULL DEFAULT 1,
                    template TEXT NOT NULL DEFAULT 'concise',
                    keywords TEXT NOT NULL DEFAULT '[]',
                    important_candidates INTEGER NOT NULL DEFAULT 1,
                    collection_window_days INTEGER NOT NULL DEFAULT 30
                );
                CREATE TABLE IF NOT EXISTS messages (
                    msg_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL,
                    sender_qq INTEGER,
                    timestamp TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    raw_digest TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, msg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_group_time
                    ON messages(group_id, timestamp);
                CREATE TABLE IF NOT EXISTS sync_state (
                    group_id INTEGER PRIMARY KEY,
                    last_timestamp TEXT,
                    backfill_completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    report_date TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    candidate_ids TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    UNIQUE(group_id, report_date)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    message_ids TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    candidate_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ignore_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_status
                    ON candidates(status, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    item_id TEXT PRIMARY KEY,
                    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id),
                    markdown_path TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS send_log (
                    send_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER REFERENCES reports(report_id),
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    attempted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT NOT NULL DEFAULT ''
                );
                """
            )

    @staticmethod
    def _utc_timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def upsert_groups(self, groups: list[GroupConfig]) -> None:
        with self.transaction():
            for group in groups:
                self.connection.execute(
                    """
                    INSERT INTO groups(
                        group_id, name, enabled, daily_summary, template,
                        keywords, important_candidates, collection_window_days
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        name=excluded.name,
                        enabled=excluded.enabled,
                        daily_summary=excluded.daily_summary,
                        template=excluded.template,
                        keywords=excluded.keywords,
                        important_candidates=excluded.important_candidates,
                        collection_window_days=excluded.collection_window_days
                    """,
                    (
                        group.group_id,
                        group.name,
                        int(group.enabled),
                        int(group.daily_summary),
                        group.template,
                        json.dumps(group.keywords, ensure_ascii=False),
                        int(group.important_candidates),
                        group.collection_window_days,
                    ),
                )

    def ingest(self, messages: Iterable[NormalizedMessage]) -> IngestResult:
        rows = list(messages)
        keys = [(row.group_id, row.msg_id) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("同一批消息中存在重复 group_id/msg_id")

        inserted = 0
        skipped = 0
        with self.transaction():
            for message in rows:
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO messages(
                        msg_id, group_id, sender_qq, timestamp, message_type, text,
                        content_json, raw_digest, source_id, device_id, collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.msg_id,
                        message.group_id,
                        message.sender_qq,
                        self._utc_timestamp(message.timestamp),
                        message.message_type,
                        message.text,
                        json.dumps(message.content_json, ensure_ascii=False),
                        message.raw_digest,
                        message.source_id,
                        message.device_id,
                        self._utc_timestamp(message.collected_at),
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    skipped += 1
        return IngestResult(inserted=inserted, skipped=skipped)

    def _row_to_message(self, row: sqlite3.Row) -> NormalizedMessage:
        return NormalizedMessage(
            msg_id=row["msg_id"],
            group_id=row["group_id"],
            sender_qq=row["sender_qq"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            message_type=row["message_type"],
            text=row["text"],
            content_json=json.loads(row["content_json"]),
            raw_digest=row["raw_digest"],
            source_id=row["source_id"],
            device_id=row["device_id"],
            collected_at=datetime.fromisoformat(row["collected_at"]),
        )

    def messages_between(
        self, group_id: int, start: datetime, end: datetime
    ) -> list[NormalizedMessage]:
        rows = self.connection.execute(
            """
            SELECT * FROM messages
            WHERE group_id=? AND timestamp>=? AND timestamp<=?
            ORDER BY timestamp, msg_id
            """,
            (group_id, self._utc_timestamp(start), self._utc_timestamp(end)),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def count_messages(self, group_id: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE group_id=?", (group_id,)
        ).fetchone()
        return int(row["total"])

    def record_knowledge_item(
        self, *, item_id: str, candidate_id: int, markdown_path: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_items(
                    item_id, candidate_id, markdown_path, written_at
                ) VALUES (?, ?, ?, ?)
                """,
                (item_id, candidate_id, markdown_path, now),
            )
            if cursor.rowcount:
                return
            row = self.connection.execute(
                """
                SELECT candidate_id, markdown_path
                FROM knowledge_items WHERE item_id=?
                """,
                (item_id,),
            ).fetchone()
        if row is None or int(row["candidate_id"]) != candidate_id or row["markdown_path"] != markdown_path:
            raise ValueError(f"知识条目 {item_id} 已绑定其他候选或路径")

    def enabled_groups(self, *, daily_summary: bool = False) -> list[GroupConfig]:
        query = "SELECT * FROM groups WHERE enabled=1"
        params: tuple = ()
        if daily_summary:
            query += " AND daily_summary=1"
        rows = self.connection.execute(query, params).fetchall()
        return [
            GroupConfig(
                group_id=row["group_id"],
                name=row["name"],
                enabled=bool(row["enabled"]),
                daily_summary=bool(row["daily_summary"]),
                template=row["template"],
                keywords=json.loads(row["keywords"]),
                important_candidates=bool(row["important_candidates"]),
                collection_window_days=int(row["collection_window_days"]),
            )
            for row in rows
        ]

    def mark_sync(
        self, *, group_id: int, last_timestamp: datetime, status: str = "active"
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO sync_state(
                    group_id, last_timestamp, status, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    last_timestamp=excluded.last_timestamp,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (group_id, self._utc_timestamp(last_timestamp), status, now),
            )

    def report_for(self, group_id: int, report_date: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM reports WHERE group_id=? AND report_date=?",
            (group_id, report_date),
        ).fetchone()

    def candidates_for_report(self, candidate_ids_json: str) -> list[int]:
        return json.loads(candidate_ids_json)

    def record_report(
        self,
        *,
        group_id: int,
        report_date: str,
        markdown_path: Path,
        json_path: Path,
        candidate_ids: list[int],
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO reports(
                    group_id, report_date, markdown_path, json_path,
                    candidate_ids, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, report_date) DO UPDATE SET
                    markdown_path=excluded.markdown_path,
                    json_path=excluded.json_path,
                    candidate_ids=excluded.candidate_ids
                """,
                (
                    group_id,
                    report_date,
                    str(markdown_path),
                    str(json_path),
                    json.dumps(candidate_ids, ensure_ascii=False),
                    now,
                ),
            )
            row = self.connection.execute(
                "SELECT report_id FROM reports WHERE group_id=? AND report_date=?",
                (group_id, report_date),
            ).fetchone()
        return int(row["report_id"])

    def start_job(self, job_type: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            cursor = self.connection.execute(
                "INSERT INTO jobs(job_type, status, started_at) VALUES (?, 'running', ?)",
                (job_type, now),
            )
        return int(cursor.lastrowid)

    def finish_job(
        self, job_id: int, status: str, error: str = ""
    ) -> None:
        if status not in {"success", "failed"}:
            raise ValueError("job status 只支持 success 或 failed")
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute(
                """
                UPDATE jobs SET status=?, finished_at=?, error=?
                WHERE job_id=?
                """,
                (status, now, error, job_id),
            )

    def close(self) -> None:
        self.connection.close()
