"""NTQQ local database collector.

Reads group messages from decrypted NTQQ SQLite databases.  Requires the
databases to be decrypted beforehand (the QQ client encrypts them at rest).
See docs/superpowers/plans/2026-08-24-qq-digest-mvp.md for setup.

Safety: opens databases in read-only URI mode, never writes to them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..models import NormalizedMessage
from .base import CollectionIncompleteError, Collector
from .protobuf import extract_message_text


_META_COLUMNS = '"40001","40020","40021","40027","40050"'


class NTQQCollector(Collector):
    """Collect messages from decrypted NTQQ local databases.

    Args:
        db_dir: Directory containing decrypted databases (nt_msg.db,
                group_info.db, etc.).
        qq_number: The QQ account number (used for path resolution only).
        timezone_name: Timezone for returned timestamps.
    """

    def __init__(
        self,
        db_dir: str | Path,
        qq_number: int = 0,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.db_dir = Path(db_dir)
        self.qq_number = qq_number
        self.timezone = ZoneInfo(timezone_name)
        self._group_names: dict[int, str] | None = None

    # ------------------------------------------------------------------
    # Group discovery
    # ------------------------------------------------------------------

    def discover_groups(self) -> list[dict]:
        """Return groups from group_info.db with message counts.

        Each dict has: group_id, name, latest_message_at, message_count_30d.
        """
        self._load_group_names()
        assert self._group_names is not None

        cutoff_ts = int(datetime.now(self.timezone).timestamp()) - 30 * 24 * 60 * 60
        gid_counts: dict[int, int] = {}
        gid_latest_ts: dict[int, int] = {}
        use_searchable = (self.db_dir / "group_msg_fts.db").exists()
        msg_db = self._open_fts_db() if use_searchable else self._open_msg_db()
        try:
            if use_searchable:
                cursor = msg_db.execute(
                    'SELECT "40021","40050" FROM group_msg_fts ORDER BY rowid DESC'
                )
            else:
                cursor = msg_db.execute(
                    f"SELECT {_META_COLUMNS} FROM group_msg_table ORDER BY rowid DESC"
                )
            for row in cursor:
                peer = row[0] if use_searchable else row[2]
                ts = row[1] if use_searchable else row[4]
                peer = peer or ""
                ts = ts or 0
                # NTQQ rowids are chronological. Once the descending scan
                # reaches data older than the requested 30-day window, stop
                # before touching unrelated historical pages.
                if ts and ts < cutoff_ts:
                    break
                if peer and str(peer).isdigit() and ts >= cutoff_ts:
                    gid = int(peer)
                    gid_counts[gid] = gid_counts.get(gid, 0) + 1
                    if ts > gid_latest_ts.get(gid, 0):
                        gid_latest_ts[gid] = ts
        except sqlite3.DatabaseError as exc:
            source = "全文库" if use_searchable else "消息库"
            raise CollectionIncompleteError(
                f"群列表{source}统计不完整: {exc}"
            ) from exc
        finally:
            msg_db.close()

        groups: list[dict] = []
        for group_id, name in self._group_names.items():
            count = gid_counts.get(group_id, 0)
            latest_ts = gid_latest_ts.get(group_id, 0)
            latest_dt = (
                datetime.fromtimestamp(latest_ts, tz=self.timezone)
                if latest_ts
                else None
            )
            groups.append(
                {
                    "group_id": group_id,
                    "name": name,
                    "latest_message_at": latest_dt,
                    "message_count_30d": count,
                }
            )
        return groups

    # ------------------------------------------------------------------
    # Message collection
    # ------------------------------------------------------------------

    def collect(
        self, group_id: int, start: datetime, end: datetime
    ) -> Iterable[NormalizedMessage]:
        """Yield NormalizedMessage objects for the given group and time window.

        Current NTQQ versions persist searchable message text in
        group_msg_fts.db. Older versions are supported through the legacy
        protobuf BLOB path in nt_msg.db.
        """
        if (self.db_dir / "group_msg_fts.db").exists():
            yield from self._collect_searchable(group_id, start, end)
            return

        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        now = datetime.now(self.timezone)

        msg_db = self._open_msg_db()
        try:
            matching_rows = self._scan_metadata(msg_db, group_id, start_ts, end_ts)
        finally:
            msg_db.close()

        yield from self._fetch_blobs(matching_rows, group_id, now)

    def _collect_searchable(
        self, group_id: int, start: datetime, end: datetime
    ) -> Iterable[NormalizedMessage]:
        """Read normalized message text from group_msg_fts.db."""
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        collected_at = datetime.now(self.timezone)
        connection = self._open_fts_db()
        rows: list[tuple] = []
        try:
            cursor = connection.execute(
                'SELECT "40001","40050","40020","40021","40027",'
                '"41701","41702","41703","41704" '
                'FROM group_msg_fts ORDER BY rowid DESC'
            )
            gid = str(group_id)
            for row in cursor:
                timestamp = int(row[1] or 0)
                if timestamp and timestamp < start_ts:
                    break
                if row[3] == gid and start_ts <= timestamp <= end_ts:
                    rows.append(row)
        except sqlite3.DatabaseError as exc:
            raise CollectionIncompleteError(f"消息全文读取不完整: {exc}") from exc
        finally:
            connection.close()

        for row in reversed(rows):
            message = self._normalize_searchable_row(row, group_id, collected_at)
            if message is not None:
                yield message

    def _normalize_searchable_row(
        self, row: tuple, group_id: int, collected_at: datetime
    ) -> NormalizedMessage | None:
        msg_id, timestamp, sender_uid, _gid_text, _gid_int, body, filename, kind, _subtype = row
        text = (body or filename or "").strip()
        if not text:
            return None

        kind_map = {
            "2": "text",
            "3": "file",
            "8": "forward",
            "9": "reply",
            "11": "link",
        }
        sender_text = str(sender_uid or "")
        sender_qq = int(sender_text) if sender_text.isdigit() else None
        content_json = {"sender_uid": sender_text} if sender_text else {}
        return NormalizedMessage(
            msg_id=str(msg_id),
            group_id=group_id,
            sender_qq=sender_qq,
            timestamp=datetime.fromtimestamp(int(timestamp), tz=self.timezone),
            message_type=kind_map.get(str(kind), "unknown"),
            text=text,
            content_json=content_json,
            source_id="ntqq_fts",
            collected_at=collected_at,
        )

    def _scan_metadata(
        self,
        msg_db: sqlite3.Connection,
        group_id: int,
        start_ts: int,
        end_ts: int,
    ) -> list[tuple[int, str, int, int]]:
        """Scan metadata columns, returning matching (msg_id, peer, sender, ts).

        The 40021 column stores the numeric group_id directly.
        Uses a rowid DESC scan for corruption resilience.
        """
        matching: list[tuple[int, str, int, int]] = []
        gid_str = str(group_id)
        try:
            cur = msg_db.cursor()
            cur.execute(
                f"SELECT rowid, {_META_COLUMNS} FROM group_msg_table ORDER BY rowid DESC"
            )
            for row in cur:
                peer = row[3] or ""
                ts = row[5] or 0
                if ts and ts < start_ts:
                    break
                if peer == gid_str and start_ts <= ts <= end_ts:
                    sender = row[4] or 0
                    matching.append((row[1], peer, sender, ts))
        except sqlite3.DatabaseError as exc:
            raise CollectionIncompleteError(f"消息元数据扫描不完整: {exc}") from exc
        return matching

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_blobs(
        self,
        rows: list[tuple[int, str, int, int]],
        group_id: int,
        collected_at: datetime,
    ) -> Iterable[NormalizedMessage]:
        """Fetch 40800 BLOBs in batches and yield NormalizedMessages."""
        msg_db = self._open_msg_db()
        try:
            mcur = msg_db.cursor()
            batch_size = 50
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                ids = [r[0] for r in batch]
                placeholders = ",".join("?" * len(ids))
                mcur.execute(
                    f'SELECT "40001","40800" FROM group_msg_table '
                    f'WHERE "40001" IN ({placeholders})',
                    ids,
                )
                blob_map: dict[int, bytes | None] = {}
                for row in mcur.fetchall():
                    blob_map[row[0]] = row[1]

                for msg_id, peer, sender, ts in batch:
                    if msg_id not in blob_map:
                        raise CollectionIncompleteError(
                            f"消息正文缺失: group_id={group_id}, msg_id={msg_id}"
                        )
                    blob = blob_map[msg_id]
                    text, msg_type = extract_message_text(blob)
                    if not text:
                        continue  # unsupported messages are intentionally omitted
                    kind = self._map_kind(msg_type)
                    yield NormalizedMessage(
                        msg_id=str(msg_id),
                        group_id=group_id,
                        sender_qq=sender if sender else None,
                        timestamp=datetime.fromtimestamp(ts, tz=self.timezone),
                        message_type=kind,
                        text=text,
                        collected_at=collected_at,
                    )
        except sqlite3.DatabaseError as exc:
            raise CollectionIncompleteError(f"消息正文读取不完整: {exc}") from exc
        finally:
            msg_db.close()

    def _map_kind(self, msg_type: str) -> str:
        mapping = {
            "text": "text",
            "image": "file",
            "file": "file",
            "reply": "reply",
            "forward": "forward",
            "voice": "file",
            "system": "system",
            "system_text": "system",
            "json": "link",
            "flash": "file",
            "emoji": "unknown",
            "markdown": "text",
        }
        return mapping.get(msg_type, "unknown")

    def _load_group_names(self) -> None:
        """Load group_id -> name from group_info.db."""
        if self._group_names is not None:
            return
        self._group_names = {}

        group_info_path = self.db_dir / "group_info.db"
        if not group_info_path.exists():
            raise FileNotFoundError(f"group_info.db not found at {group_info_path}")

        conn = sqlite3.connect(f"file:{group_info_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT "60001","60007" FROM group_list')
        for row in cur.fetchall():
            gid = row["60001"]
            name = row["60007"]
            self._group_names[gid] = name
        conn.close()

    def _open_msg_db(self) -> sqlite3.Connection:
        msg_db_path = self.db_dir / "nt_msg.db"
        if not msg_db_path.exists():
            raise FileNotFoundError(f"nt_msg.db not found at {msg_db_path}")
        return sqlite3.connect(f"file:{msg_db_path}?mode=ro", uri=True)

    def _open_fts_db(self) -> sqlite3.Connection:
        path = self.db_dir / "group_msg_fts.db"
        if not path.exists():
            raise FileNotFoundError(f"group_msg_fts.db not found at {path}")
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
