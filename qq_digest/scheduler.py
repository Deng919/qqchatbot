from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
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
    target_date: str | None = None,
) -> DailyRetryState:
    """Return whether today's persisted daily job is due to run or retry."""
    if now.tzinfo is None:
        raise ValueError("now 必须包含时区")

    local_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    target = local_start.replace(hour=target_hour, minute=target_minute)
    utc_start = local_start.astimezone(dt_timezone.utc).isoformat()
    utc_end = local_end.astimezone(dt_timezone.utc).isoformat()
    has_target_date = any(
        column[1] == "target_date"
        for column in connection.execute("PRAGMA table_info(jobs)")
    )
    if has_target_date:
        rows = connection.execute(
            """SELECT status, started_at, finished_at FROM jobs
               WHERE job_type='daily_digest'
                 AND (target_date=? OR
                      (target_date IS NULL AND started_at>=? AND started_at<?))
               ORDER BY job_id""",
            (target_date or local_start.date().isoformat(), utc_start, utc_end),
        ).fetchall()
    else:
        rows = connection.execute(
            """SELECT status, started_at, finished_at FROM jobs
               WHERE job_type='daily_digest' AND started_at>=? AND started_at<?
               ORDER BY job_id""",
            (utc_start, utc_end),
        ).fetchall()
    attempts = len(rows)
    succeeded = bool(rows and rows[-1][0] == "success")
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


def pending_catchup_date(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    mode: str,
    max_attempts: int,
    retry_interval_minutes: int,
    lookback_days: int = 3,
) -> date | None:
    """Find a recent missed report day without consuming today's retry budget."""
    if now.tzinfo is None:
        raise ValueError("now 必须包含时区")
    timezone = now.tzinfo
    scheduled_date = summary_window(now, mode, timezone)[0].date()
    rows = connection.execute(
        """SELECT status, started_at, finished_at, target_date FROM jobs
           WHERE job_type='daily_digest' ORDER BY job_id"""
    ).fetchall()
    by_date: dict[date, list[sqlite3.Row]] = {}
    for row in rows:
        if row["target_date"]:
            report_date = date.fromisoformat(row["target_date"])
        elif row["started_at"]:
            started = datetime.fromisoformat(row["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=dt_timezone.utc)
            report_date = summary_window(started, mode, timezone)[0].date()
        else:
            continue
        by_date.setdefault(report_date, []).append(row)

    for offset in range(1, lookback_days + 1):
        candidate = scheduled_date - timedelta(days=offset)
        attempts = by_date.get(candidate, [])
        if attempts and attempts[-1]["status"] == "success":
            continue
        attempts_today = 0
        for row in attempts:
            started = datetime.fromisoformat(row["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=dt_timezone.utc)
            if started.astimezone(timezone).date() == now.date():
                attempts_today += 1
        if attempts_today >= max_attempts:
            continue
        if attempts:
            latest = attempts[-1]
            timestamp = latest["finished_at"] or latest["started_at"]
            attempted_at = datetime.fromisoformat(timestamp)
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=dt_timezone.utc)
            if latest["status"] == "running" and now < attempted_at.astimezone(
                timezone
            ) + timedelta(hours=1):
                continue
            if now < attempted_at.astimezone(timezone) + timedelta(
                minutes=retry_interval_minutes
            ):
                continue
        return candidate
    return None


def run_at_for_report_date(report_date: date, mode: str, timezone: ZoneInfo) -> datetime:
    """Choose a complete historical window for a missed daily report."""
    if mode == "today":
        return datetime.combine(report_date, time.max, tzinfo=timezone)
    if mode == "previous_day":
        return datetime.combine(report_date + timedelta(days=1), time.min, tzinfo=timezone)
    raise ValueError("mode 只支持 today 或 previous_day")


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
