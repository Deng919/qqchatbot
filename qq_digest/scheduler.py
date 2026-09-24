from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DailyRetryState:
    attempts: int
    due: bool
    succeeded: bool
    next_retry_at: datetime | None = None


def daily_retry_state(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    target_hour: int,
    target_minute: int,
    max_attempts: int,
    retry_interval_minutes: int,
) -> DailyRetryState:
    """Return whether today's persisted daily job is due to run or retry."""
    if now.tzinfo is None:
        raise ValueError("now 必须包含时区")

    local_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    target = local_start.replace(hour=target_hour, minute=target_minute)
    rows = connection.execute(
        """
        SELECT status, started_at, finished_at
        FROM jobs
        WHERE job_type='daily_digest' AND started_at>=? AND started_at<?
        ORDER BY job_id
        """,
        (
            local_start.astimezone(dt_timezone.utc).isoformat(),
            local_end.astimezone(dt_timezone.utc).isoformat(),
        ),
    ).fetchall()
    attempts = len(rows)
    succeeded = any(row[0] == "success" for row in rows)
    if now < target or succeeded or attempts >= max_attempts:
        return DailyRetryState(attempts=attempts, due=False, succeeded=succeeded)
    if not rows:
        return DailyRetryState(attempts=0, due=True, succeeded=False)

    latest = rows[-1]
    latest_timestamp = latest[2] or latest[1]
    if not latest_timestamp:
        return DailyRetryState(attempts=attempts, due=False, succeeded=False)
    completed_at = datetime.fromisoformat(latest_timestamp)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=dt_timezone.utc)
    next_retry_at = completed_at + timedelta(minutes=retry_interval_minutes)
    return DailyRetryState(
        attempts=attempts,
        due=now.astimezone(dt_timezone.utc) >= next_retry_at.astimezone(dt_timezone.utc),
        succeeded=False,
        next_retry_at=next_retry_at,
    )


def summary_window(
    now: datetime, mode: str, timezone: ZoneInfo
) -> tuple[datetime, datetime]:
    local_now = now.astimezone(timezone)
    if mode == "today":
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, local_now
    if mode == "previous_day":
        next_day = datetime.combine(local_now.date(), time.min, tzinfo=timezone)
        return next_day - timedelta(days=1), next_day - timedelta(microseconds=1)
    raise ValueError("mode 只支持 today 或 previous_day")
