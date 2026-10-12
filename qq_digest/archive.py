from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .archive_schema import initialize_archive_schema
from .models import GroupConfig, NormalizedMessage
from .message_search import install_message_index
from .sender_identity import refresh_message_sender


@dataclass(frozen=True)
class IngestResult:
    inserted: int
    skipped: int


@dataclass(frozen=True)
class GroupDeletionResult:
    group_id: int
    deleted: dict[str, int]
    report_paths: tuple[str, ...]


@dataclass(frozen=True)
class QueuedNotification:
    send_id: int
    report_id: int | None
    channel: str
    recipient: str
    payload: dict
    retry_count: int


class Archive:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self.message_search_indexed = install_message_index(self.connection)

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
            initialize_archive_schema(self.connection)

    def enqueue_notification(
        self,
        *,
        report_id: int | None,
        channel: str,
        recipient: str,
        payload: dict,
        now: datetime | None = None,
    ) -> int:
        timestamp = self._utc_timestamp(now or datetime.now(timezone.utc))
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self.transaction():
            existing = self.connection.execute(
                """
                SELECT send_id FROM send_log
                WHERE report_id IS ? AND channel=? AND recipient=?
                  AND status IN ('pending_send', 'sending', 'success')
                ORDER BY send_id DESC LIMIT 1
                """,
                (report_id, channel, recipient),
            ).fetchone()
            if existing is not None:
                return int(existing["send_id"])
            cursor = self.connection.execute(
                """
                INSERT INTO send_log(
                    report_id, channel, status, error, attempted_at,
                    recipient, payload_json, retry_count, next_attempt_at, updated_at
                ) VALUES (?, ?, 'pending_send', '', ?, ?, ?, 0, ?, ?)
                """,
                (report_id, channel, timestamp, recipient, payload_json, timestamp, timestamp),
            )
        return int(cursor.lastrowid)

    def due_notifications(
        self, *, now: datetime | None = None, limit: int = 50
    ) -> list[QueuedNotification]:
        timestamp = self._utc_timestamp(now or datetime.now(timezone.utc))
        rows = self.connection.execute(
            """
            SELECT * FROM send_log
            WHERE status='pending_send'
              AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            ORDER BY send_id LIMIT ?
            """,
            (timestamp, limit),
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    @staticmethod
    def _row_to_notification(row: sqlite3.Row) -> QueuedNotification:
        return QueuedNotification(
            send_id=int(row["send_id"]),
            report_id=int(row["report_id"]) if row["report_id"] is not None else None,
            channel=row["channel"],
            recipient=row["recipient"],
            payload=json.loads(row["payload_json"] or "{}"),
            retry_count=int(row["retry_count"]),
        )

    def claim_due_notifications(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
        lease_seconds: int = 300,
    ) -> list[QueuedNotification]:
        current = now or datetime.now(timezone.utc)
        timestamp = self._utc_timestamp(current)
        stale_timestamp = self._utc_timestamp(
            current - timedelta(seconds=lease_seconds)
        )
        claimed: list[QueuedNotification] = []
        with self.transaction():
            rows = self.connection.execute(
                """
                SELECT * FROM send_log
                WHERE (
                    status='pending_send'
                    AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                ) OR (
                    status='sending' AND updated_at<=?
                )
                ORDER BY send_id LIMIT ?
                """,
                (timestamp, stale_timestamp, limit),
            ).fetchall()
            for row in rows:
                cursor = self.connection.execute(
                    """
                    UPDATE send_log SET status='sending', updated_at=?
                    WHERE send_id=? AND (
                        (status='pending_send' AND (next_attempt_at IS NULL OR next_attempt_at<=?))
                        OR (status='sending' AND updated_at<=?)
                    )
                    """,
                    (timestamp, row["send_id"], timestamp, stale_timestamp),
                )
                if cursor.rowcount:
                    claimed.append(self._row_to_notification(row))
        return claimed

    def mark_notification_success(
        self, send_id: int, *, now: datetime | None = None
    ) -> None:
        timestamp = self._utc_timestamp(now or datetime.now(timezone.utc))
        with self.transaction():
            self.connection.execute(
                """
                UPDATE send_log
                SET status='success', error='', attempted_at=?,
                    next_attempt_at=NULL, updated_at=?
                WHERE send_id=?
                """,
                (timestamp, timestamp, send_id),
            )

    def retry_failed_notification(
        self, send_id: int, *, now: datetime | None = None
    ) -> bool:
        timestamp = self._utc_timestamp(now or datetime.now(timezone.utc))
        with self.transaction():
            cursor = self.connection.execute(
                """UPDATE send_log SET status='pending_send', retry_count=0,
                   manual_retry_count=manual_retry_count+1, next_attempt_at=?,
                   updated_at=? WHERE send_id=? AND status='failed'
                   AND NOT EXISTS (
                       SELECT 1 FROM send_log AS other
                       WHERE other.send_id<>send_log.send_id
                         AND other.report_id IS send_log.report_id
                         AND other.channel=send_log.channel
                         AND other.recipient=send_log.recipient
                         AND other.status IN ('pending_send','sending','success')
                   )""",
                (timestamp, timestamp, send_id),
            )
        return bool(cursor.rowcount)

    def mark_notification_retry(
        self,
        send_id: int,
        *,
        error: str,
        next_attempt_at: datetime,
        max_attempts: int,
        now: datetime | None = None,
    ) -> None:
        timestamp = self._utc_timestamp(now or datetime.now(timezone.utc))
        next_timestamp = self._utc_timestamp(next_attempt_at)
        with self.transaction():
            row = self.connection.execute(
                "SELECT retry_count FROM send_log WHERE send_id=?", (send_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"通知 {send_id} 不存在")
            retry_count = int(row["retry_count"]) + 1
            status = "failed" if retry_count >= max_attempts else "pending_send"
            self.connection.execute(
                """
                UPDATE send_log
                SET status=?, error=?, attempted_at=?, retry_count=?,
                    next_attempt_at=?, updated_at=?
                WHERE send_id=?
                """,
                (
                    status,
                    error,
                    timestamp,
                    retry_count,
                    None if status == "failed" else next_timestamp,
                    timestamp,
                    send_id,
                ),
            )

    @staticmethod
    def _utc_timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def seed_groups(self, groups: list[GroupConfig]) -> None:
        """Initialize YAML groups once; SQLite owns subsequent edits/deletions."""
        with self.transaction():
            for group in groups:
                if self.connection.execute(
                    "SELECT 1 FROM seeded_groups WHERE group_id=?", (group.group_id,)
                ).fetchone():
                    continue
                self.connection.execute(
                    """INSERT OR IGNORE INTO groups(
                        group_id, name, enabled, daily_summary, template, keywords,
                        important_candidates, collection_window_days, category,read_images,read_files
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (group.group_id, group.name, int(group.enabled),
                     int(group.daily_summary), group.template,
                     json.dumps(group.keywords, ensure_ascii=False),
                     int(group.important_candidates), group.collection_window_days,
                     group.category,int(group.read_images),int(group.read_files)),
                )
                self.connection.execute(
                    "INSERT INTO seeded_groups(group_id) VALUES (?)", (group.group_id,)
                )

    def upsert_groups(self, groups: list[GroupConfig]) -> None:
        with self.transaction():
            for group in groups:
                self.connection.execute(
                    """
                    INSERT INTO groups(
                        group_id, name, enabled, daily_summary, template,
                        keywords, important_candidates, collection_window_days,
                        category,read_images,read_files
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        name=excluded.name,
                        enabled=excluded.enabled,
                        daily_summary=excluded.daily_summary,
                        template=excluded.template,
                        keywords=excluded.keywords,
                        important_candidates=excluded.important_candidates,
                        collection_window_days=excluded.collection_window_days,
                        category=excluded.category,
                        read_images=excluded.read_images,read_files=excluded.read_files
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
                        group.category,
                        int(group.read_images),int(group.read_files),
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
            # Explicit monotonically increasing rowids survive deleting the newest
            # messages, preserving consumers' durable scan cursors.
            self.connection.execute("""UPDATE message_ingest_sequence SET high_water=MAX(high_water,
                (SELECT COALESCE(MAX(rowid),0) FROM messages)) WHERE singleton=1""")
            high_water = self.connection.execute(
                "SELECT high_water FROM message_ingest_sequence WHERE singleton=1"
            ).fetchone()[0]
            for message in rows:
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO messages(
                        rowid, msg_id, group_id, sender_qq, timestamp, message_type, text,
                        content_json, raw_digest, source_id, device_id, collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        high_water + 1,
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
                    high_water += 1
                    self.connection.execute(
                        "UPDATE message_ingest_sequence SET high_water=? WHERE singleton=1",
                        (high_water,),
                    )
                else:
                    skipped += 1
                    from .attachment_sources import refresh_attachment_metadata
                    refresh_attachment_metadata(self.connection,message)
                    refresh_message_sender(self.connection,message)
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

    def messages_in_window(
        self, group_id: int, start: datetime, end: datetime
    ) -> list[NormalizedMessage]:
        rows = self.connection.execute(
            """
            SELECT * FROM messages
            WHERE group_id=? AND timestamp>=? AND timestamp<?
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

    def all_groups(self) -> list[GroupConfig]:
        rows = self.connection.execute("SELECT * FROM groups ORDER BY group_id").fetchall()
        return [self._row_to_group(row) for row in rows]

    def enabled_groups(self, *, daily_summary: bool = False) -> list[GroupConfig]:
        query = "SELECT * FROM groups WHERE enabled=1"
        params: tuple = ()
        if daily_summary:
            query += " AND daily_summary=1"
        rows = self.connection.execute(query, params).fetchall()
        return [self._row_to_group(row) for row in rows]

    def _row_to_group(self, row) -> GroupConfig:
        return GroupConfig(
            group_id=row["group_id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            daily_summary=bool(row["daily_summary"]),
            template=row["template"],
            keywords=json.loads(row["keywords"]),
            important_candidates=bool(row["important_candidates"]),
            collection_window_days=int(row["collection_window_days"]),
            read_images=bool(row['read_images']),read_files=bool(row['read_files']),
            category=row["category"] if "category" in row.keys() else "general",
        )
    def mark_sync(
        self, *, group_id: int, last_timestamp: datetime, status: str = "active"
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO sync_state(
                    group_id, last_timestamp, status, updated_at, error, last_success_at
                ) VALUES (?, ?, ?, ?, '', ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    last_timestamp=CASE
                        WHEN sync_state.last_timestamp IS NULL
                             OR sync_state.last_timestamp < excluded.last_timestamp
                        THEN excluded.last_timestamp
                        ELSE sync_state.last_timestamp
                    END,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    error='',
                    last_success_at=excluded.last_success_at
                """,
                (group_id, self._utc_timestamp(last_timestamp), status, now, now),
            )

    def mark_sync_failure(self, *, group_id: int, status: str, error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO sync_state(group_id, last_timestamp, status, updated_at, error)
                VALUES (?, NULL, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    error=excluded.error
                """,
                (group_id, status, now, error[:1000]),
            )

    def mark_manual_collect_success(
        self, *, group_id: int, status: str = "manual_repair_completed"
    ) -> None:
        """Record a successful limited-window collection without moving the sync cursor."""
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO sync_state(
                    group_id, last_timestamp, status, updated_at, error, last_success_at
                ) VALUES (?, NULL, ?, ?, '', ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    status=CASE WHEN sync_state.status='active' THEN 'active'
                                ELSE excluded.status END,
                    updated_at=excluded.updated_at,
                    error='',
                    last_success_at=excluded.last_success_at
                """,
                (group_id, status, now, now),
            )

    def report_for(self, group_id: int, report_date: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM reports WHERE group_id=? AND report_date=?",
            (group_id, report_date),
        ).fetchone()

    def manual_report_for(
        self,
        group_id: int,
        start_date: str,
        end_date: str,
        detail_mode: str,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM manual_reports
            WHERE group_id=? AND start_date=? AND end_date=? AND detail_mode=?
            """,
            (group_id, start_date, end_date, detail_mode),
        ).fetchone()

    def manual_report_by_id(self, manual_report_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM manual_reports WHERE manual_report_id=?",
            (manual_report_id,),
        ).fetchone()

    def candidates_for_report(self, candidate_ids_json: str) -> list[int]:
        return json.loads(candidate_ids_json)

    def delete_pending_candidates(self, candidate_ids: list[int]) -> int:
        if not candidate_ids:
            return 0
        placeholders = ",".join("?" for _ in candidate_ids)
        with self.transaction():
            cursor = self.connection.execute(
                f"DELETE FROM candidates WHERE status='pending' "
                f"AND candidate_id IN ({placeholders})",
                candidate_ids,
            )
        return int(cursor.rowcount)

    def delete_unreferenced_pending_candidates_in_transaction(
        self, candidate_ids: list[int]
    ) -> int:
        """Collect old drafts only after all new report references are recorded."""
        deleted = 0
        for candidate_id in set(candidate_ids):
            cursor = self.connection.execute(
                """DELETE FROM candidates
                   WHERE candidate_id=? AND status='pending'
                     AND NOT EXISTS (
                       SELECT 1 FROM reports, json_each(reports.candidate_ids) ref
                       WHERE CAST(ref.value AS INTEGER)=candidates.candidate_id
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM manual_reports, json_each(manual_reports.candidate_ids) ref
                       WHERE CAST(ref.value AS INTEGER)=candidates.candidate_id
                     )""",
                (candidate_id,),
            )
            deleted += cursor.rowcount
        return deleted

    def delete_group(self, group_id: int) -> GroupDeletionResult:
        group = self.connection.execute(
            "SELECT group_id FROM groups WHERE group_id=?", (group_id,)
        ).fetchone()
        if group is None:
            raise KeyError(f"群 {group_id} 不存在")

        report_rows = self.connection.execute(
            "SELECT report_id, markdown_path, json_path FROM reports WHERE group_id=?",
            (group_id,),
        ).fetchall()
        manual_report_rows = self.connection.execute(
            """
            SELECT manual_report_id, markdown_path, json_path
            FROM manual_reports WHERE group_id=?
            """,
            (group_id,),
        ).fetchall()
        report_ids = [int(row["report_id"]) for row in report_rows]
        report_paths = tuple(
            path
            for row in (*report_rows, *manual_report_rows)
            for path in (row["markdown_path"], row["json_path"])
            if path
        )
        candidate_rows = self.connection.execute(
            "SELECT candidate_id FROM candidates WHERE group_id=?", (group_id,)
        ).fetchall()
        candidate_ids = [int(row["candidate_id"]) for row in candidate_rows]
        deleted: dict[str, int] = {}

        def delete_where_ids(table: str, column: str, values: list[int]) -> int:
            if not values:
                return 0
            placeholders = ",".join("?" for _ in values)
            cursor = self.connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values
            )
            return int(cursor.rowcount)

        with self.transaction():
            deleted["reminder_events"] = int(self.connection.execute(
                "DELETE FROM reminder_events WHERE group_id=?", (group_id,)
            ).rowcount)
            deleted["reminder_resource_baselines"] = int(self.connection.execute(
                "DELETE FROM reminder_resource_baselines WHERE group_id=?", (group_id,)
            ).rowcount)
            # A last selected group disappearing must never broaden a rule to all groups.
            for rule in self.connection.execute(
                "SELECT rule_id,values_json FROM reminder_rules WHERE deleted=0"
            ).fetchall():
                values = json.loads(rule["values_json"])
                if group_id in values.get("group_ids", []):
                    values["group_ids"].remove(group_id)
                    if not values["group_ids"]:
                        values["enabled"] = False
                    self.connection.execute(
                        "UPDATE reminder_rules SET values_json=?,revision=revision+1 WHERE rule_id=?",
                        (json.dumps(values, ensure_ascii=False), rule["rule_id"]),
                    )
            deleted["send_log"] = delete_where_ids("send_log", "report_id", report_ids)
            deleted["knowledge_items"] = delete_where_ids(
                "knowledge_items", "candidate_id", candidate_ids
            )
            cursor = self.connection.execute(
                "DELETE FROM task_suggestion_decisions WHERE group_id=?", (group_id,)
            )
            deleted["task_suggestion_decisions"] = int(cursor.rowcount)
            self.connection.execute('UPDATE tracked_topics SET revision=revision+1 WHERE topic_id IN '
                '(SELECT topic_id FROM topic_discussions WHERE group_id=?)', (group_id,))
            cursor = self.connection.execute('DELETE FROM topic_discussions WHERE group_id=?', (group_id,))
            deleted['topic_discussions'] = int(cursor.rowcount)
            for table in (
                "bookmarks",
                "tasks",
                "manual_reports",
                "candidates",
                "reports",
                "messages",
                "sync_state",
                "groups",
            ):
                cursor = self.connection.execute(
                    f"DELETE FROM {table} WHERE group_id=?", (group_id,)
                )
                deleted[table] = int(cursor.rowcount)

        return GroupDeletionResult(
            group_id=group_id,
            deleted=deleted,
            report_paths=report_paths,
        )

    def record_report(self, **kwargs) -> int:
        with self.transaction():
            return self.record_report_in_transaction(**kwargs)

    def record_report_in_transaction(
        self,
        *,
        group_id: int,
        report_date: str,
        markdown_path: Path,
        json_path: Path,
        candidate_ids: list[int],
        effective_template: str = "",
        input_fingerprint: str = "",
        source_message_count: int = 0,
        revision_markdown: str | None = None,
        revision_payload: dict | None = None,
        revision_reason: str = "AI 生成或因输入/配置变化重新生成",
    ) -> int:
        from .report_revisions import ReportRevisionService
        revisions = ReportRevisionService(self)
        existing = self.report_for(group_id, report_date)
        if revision_markdown is not None and existing is not None:
            revisions.capture_legacy_in_transaction('daily', existing['report_id'])
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """
            INSERT INTO reports(
                group_id, report_date, markdown_path, json_path,
                candidate_ids, effective_template, input_fingerprint,
                source_message_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, report_date) DO UPDATE SET
                markdown_path=excluded.markdown_path,
                json_path=excluded.json_path,
                candidate_ids=excluded.candidate_ids,
                effective_template=excluded.effective_template,
                input_fingerprint=excluded.input_fingerprint,
                source_message_count=excluded.source_message_count,
                created_at=excluded.created_at
            """,
            (
                group_id,
                report_date,
                str(markdown_path),
                str(json_path),
                json.dumps(candidate_ids, ensure_ascii=False),
                effective_template,
                input_fingerprint,
                source_message_count,
                now,
            ),
        )
        row = self.connection.execute(
            "SELECT report_id FROM reports WHERE group_id=? AND report_date=?",
            (group_id, report_date),
        ).fetchone()
        report_id = int(row["report_id"])
        if revision_markdown is not None:
            revisions.record_generated_in_transaction('daily', report_id, revision_markdown,
                                                      revision_payload, revision_reason)
        return report_id

    def record_manual_report(
        self,
        *,
        group_id: int,
        start_date: str,
        end_date: str,
        detail_mode: str,
        effective_template: str,
        markdown_path: Path,
        json_path: Path,
        candidate_ids: list[int],
        input_fingerprint: str,
        source_message_count: int,
        revision_markdown: str | None = None,
        revision_payload: dict | None = None,
        revision_reason: str = "AI 生成或因输入/配置变化重新生成",
    ) -> int:
        with self.transaction():
            return self.record_manual_report_in_transaction(
                group_id=group_id,
                start_date=start_date,
                end_date=end_date,
                detail_mode=detail_mode,
                effective_template=effective_template,
                markdown_path=markdown_path,
                json_path=json_path,
                candidate_ids=candidate_ids,
                input_fingerprint=input_fingerprint,
                source_message_count=source_message_count,
                revision_markdown=revision_markdown,
                revision_payload=revision_payload,
                revision_reason=revision_reason,
            )

    def record_manual_report_in_transaction(
        self,
        *,
        group_id: int,
        start_date: str,
        end_date: str,
        detail_mode: str,
        effective_template: str,
        markdown_path: Path,
        json_path: Path,
        candidate_ids: list[int],
        input_fingerprint: str,
        source_message_count: int,
        revision_markdown: str | None = None,
        revision_payload: dict | None = None,
        revision_reason: str = "AI 生成或因输入/配置变化重新生成",
    ) -> int:
        from .report_revisions import ReportRevisionService
        revisions = ReportRevisionService(self)
        existing = self.manual_report_for(group_id, start_date, end_date, detail_mode)
        if revision_markdown is not None and existing is not None:
            revisions.capture_legacy_in_transaction('range', existing['manual_report_id'])
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """
            INSERT INTO manual_reports(
                group_id, start_date, end_date, detail_mode, effective_template,
                markdown_path, json_path, candidate_ids, input_fingerprint,
                source_message_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, start_date, end_date, detail_mode) DO UPDATE SET
                effective_template=excluded.effective_template,
                markdown_path=excluded.markdown_path,
                json_path=excluded.json_path,
                candidate_ids=excluded.candidate_ids,
                input_fingerprint=excluded.input_fingerprint,
                source_message_count=excluded.source_message_count,
                updated_at=excluded.updated_at
            """,
            (
                group_id,
                start_date,
                end_date,
                detail_mode,
                effective_template,
                str(markdown_path),
                str(json_path),
                json.dumps(candidate_ids, ensure_ascii=False),
                input_fingerprint,
                source_message_count,
                now,
                now,
            ),
        )
        row = self.manual_report_for(group_id, start_date, end_date, detail_mode)
        if row is None:
            raise RuntimeError("范围报告写入后无法读取")
        report_id = int(row["manual_report_id"])
        if revision_markdown is not None:
            revisions.record_generated_in_transaction('range', report_id, revision_markdown,
                                                      revision_payload, revision_reason)
        return report_id

    def start_job(
        self, job_type: str, *, target_date: str | None = None,
        retry_of_job_id: int | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            cursor = self.connection.execute(
                """INSERT INTO jobs(job_type, status, started_at, target_date, retry_of_job_id)
                   VALUES (?, 'running', ?, ?, ?)""",
                (job_type, now, target_date, retry_of_job_id),
            )
        return int(cursor.lastrowid)

    def finish_job(
        self, job_id: int, status: str, error: str = "", *,
        group_outcomes: dict[int, str] | None = None,
    ) -> None:
        if status not in {"success", "partial_success", "failed"}:
            raise ValueError("job status 只支持 success、partial_success 或 failed")
        now = datetime.now(timezone.utc).isoformat()
        outcomes = group_outcomes or {}
        if any(value not in {"success", "skipped", "failed"} for value in outcomes.values()):
            raise ValueError("群任务状态无效")
        with self.transaction():
            self.connection.execute(
                """
                UPDATE jobs SET status=?, finished_at=?, error=?
                WHERE job_id=?
                """,
                (status, now, error, job_id),
            )
            self.connection.executemany(
                """INSERT OR REPLACE INTO job_group_results(job_id,group_id,status)
                   VALUES (?,?,?)""",
                [(job_id, group_id, outcome) for group_id, outcome in outcomes.items()],
            )

    def close(self) -> None:
        self.connection.close()
