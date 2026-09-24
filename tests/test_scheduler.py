from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3

from qq_digest.scheduler import daily_retry_state, summary_window


def test_summary_window_today():
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    start, end = summary_window(now, "today", ZoneInfo("Asia/Shanghai"))

    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert end == now


def test_summary_window_applies_timezone():
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("UTC"))
    start, end = summary_window(now, "today", ZoneInfo("Asia/Shanghai"))

    assert start == datetime(2026, 8, 25, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert end == datetime(2026, 8, 25, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_summary_window_previous_day():
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    start, end = summary_window(now, "previous_day", ZoneInfo("Asia/Shanghai"))

    assert start == datetime(2026, 8, 23, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert end == datetime(
        2026, 8, 23, 23, 59, 59, 999999, tzinfo=ZoneInfo("Asia/Shanghai")
    )


def test_daily_retry_waits_then_retries_failed_job():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE jobs(job_id INTEGER PRIMARY KEY, job_type TEXT, status TEXT, started_at TEXT, finished_at TEXT, error TEXT)"
    )
    connection.execute(
        "INSERT INTO jobs VALUES (1,'daily_digest','failed','2026-08-24T14:00:00+00:00','2026-08-24T14:01:00+00:00','429')"
    )
    timezone = ZoneInfo("Asia/Shanghai")

    waiting = daily_retry_state(
        connection,
        datetime(2026, 8, 24, 22, 10, tzinfo=timezone),
        target_hour=22,
        target_minute=0,
        max_attempts=3,
        retry_interval_minutes=15,
    )
    due = daily_retry_state(
        connection,
        datetime(2026, 8, 24, 22, 17, tzinfo=timezone),
        target_hour=22,
        target_minute=0,
        max_attempts=3,
        retry_interval_minutes=15,
    )

    assert waiting.attempts == 1
    assert waiting.due is False
    assert due.due is True


def test_daily_retry_stops_after_success_or_attempt_limit():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE jobs(job_id INTEGER PRIMARY KEY, job_type TEXT, status TEXT, started_at TEXT, finished_at TEXT, error TEXT)"
    )
    for job_id, minute in enumerate((0, 20, 40), 1):
        connection.execute(
            "INSERT INTO jobs VALUES (?, 'daily_digest', 'failed', ?, ?, '429')",
            (
                job_id,
                f"2026-08-24T14:{minute:02d}:00+00:00",
                f"2026-08-24T14:{minute:02d}:30+00:00",
            ),
        )
    timezone = ZoneInfo("Asia/Shanghai")
    limited = daily_retry_state(
        connection,
        datetime(2026, 8, 24, 23, 0, tzinfo=timezone),
        target_hour=22,
        target_minute=0,
        max_attempts=3,
        retry_interval_minutes=15,
    )
    connection.execute("UPDATE jobs SET status='success' WHERE job_id=2")
    successful = daily_retry_state(
        connection,
        datetime(2026, 8, 24, 23, 0, tzinfo=timezone),
        target_hour=22,
        target_minute=0,
        max_attempts=5,
        retry_interval_minutes=15,
    )

    assert limited.due is False
    assert limited.attempts == 3
    assert successful.due is False
    assert successful.succeeded is True


def test_daily_retry_retries_partial_success_after_interval():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE jobs(job_id INTEGER PRIMARY KEY, job_type TEXT, status TEXT, "
        "started_at TEXT, finished_at TEXT, error TEXT)"
    )
    connection.execute(
        "INSERT INTO jobs VALUES "
        "(1,'daily_digest','partial_success','2026-08-24T14:00:00+00:00',"
        "'2026-08-24T14:01:00+00:00','group 123')"
    )
    timezone = ZoneInfo("Asia/Shanghai")

    waiting = daily_retry_state(
        connection,
        datetime(2026, 8, 24, 22, 10, tzinfo=timezone),
        target_hour=22,
        target_minute=0,
        max_attempts=3,
        retry_interval_minutes=15,
    )
    due = daily_retry_state(
        connection,
        datetime(2026, 8, 24, 22, 17, tzinfo=timezone),
        target_hour=22,
        target_minute=0,
        max_attempts=3,
        retry_interval_minutes=15,
    )

    assert waiting.due is False
    assert due.due is True
    assert due.succeeded is False
