from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3

from qq_digest.scheduler import daily_retry_state, summary_window
from qq_digest.archive import Archive


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
    assert successful.due is True
    assert successful.succeeded is False


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


def test_catchup_job_does_not_consume_todays_scheduled_attempt(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.connection.execute(
        """INSERT INTO jobs(job_type,status,started_at,finished_at,target_date)
           VALUES ('daily_digest','success','2026-09-28T05:00:00+00:00',
                   '2026-09-28T05:01:00+00:00','2026-09-27')"""
    )

    state = daily_retry_state(
        archive.connection,
        datetime(2026, 9, 28, 22, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        target_hour=22,
        target_minute=0,
        max_attempts=3,
        retry_interval_minutes=15,
        target_date="2026-09-28",
    )

    assert state.due is True
    assert state.attempts == 0
    archive.close()


def test_catchup_finds_previous_failed_day_and_skips_success(tmp_path):
    from qq_digest.scheduler import pending_catchup_date

    archive = Archive.open(tmp_path / "archive.sqlite")
    failed = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(failed, "failed", "temporary key error")
    archive.connection.execute(
        """UPDATE jobs SET started_at='2026-09-27T14:53:00+00:00',
               finished_at='2026-09-27T14:53:01+00:00' WHERE job_id=?""",
        (failed,),
    )
    now = datetime(2026, 9, 28, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert pending_catchup_date(
        archive.connection, now, mode="today", max_attempts=3,
        retry_interval_minutes=15,
    ).isoformat() == "2026-09-27"

    successful = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(successful, "success")
    assert pending_catchup_date(
        archive.connection, now, mode="today", max_attempts=3,
        retry_interval_minutes=15,
    ).isoformat() == "2026-09-26"
    archive.close()


def test_catchup_recognizes_failed_job_from_before_target_date_migration(tmp_path):
    from qq_digest.scheduler import pending_catchup_date

    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.connection.execute(
        """INSERT INTO jobs(job_type,status,started_at,finished_at,error)
           VALUES ('daily_digest','failed','2026-09-27T14:53:00+00:00',
                   '2026-09-27T14:53:01+00:00','old key file failure')"""
    )
    now = datetime(2026, 9, 28, 13, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert pending_catchup_date(
        archive.connection, now, mode="today", max_attempts=3,
        retry_interval_minutes=15,
    ).isoformat() == "2026-09-27"
    archive.close()


def test_catchup_run_at_covers_the_complete_historical_day():
    from datetime import date
    from qq_digest.scheduler import run_at_for_report_date

    timezone = ZoneInfo("Asia/Shanghai")
    target = date(2026, 9, 27)
    today_run = run_at_for_report_date(target, "today", timezone)
    previous_day_run = run_at_for_report_date(target, "previous_day", timezone)

    assert summary_window(today_run, "today", timezone)[0].date() == target
    assert today_run.hour == 23 and today_run.minute == 59
    assert summary_window(previous_day_run, "previous_day", timezone)[0].date() == target


def test_catchup_retries_when_a_later_attempt_is_partial(tmp_path):
    from qq_digest.scheduler import pending_catchup_date

    archive = Archive.open(tmp_path / "archive.sqlite")
    first = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(first, "success")
    second = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(second, "partial_success")
    archive.connection.execute(
        """UPDATE jobs SET started_at='2026-09-27T14:53:00+00:00',
               finished_at='2026-09-27T14:54:00+00:00'"""
    )
    now = datetime(2026, 9, 28, 13, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert pending_catchup_date(
        archive.connection, now, mode="today", max_attempts=3,
        retry_interval_minutes=15,
    ).isoformat() == "2026-09-27"
    archive.close()


def test_catchup_includes_all_unattempted_days_in_lookback(tmp_path):
    from qq_digest.scheduler import pending_catchup_date

    archive = Archive.open(tmp_path / "archive.sqlite")
    now = datetime(2026, 9, 28, 13, tzinfo=ZoneInfo("Asia/Shanghai"))

    for expected in ("2026-09-27", "2026-09-26", "2026-09-25"):
        candidate = pending_catchup_date(
            archive.connection, now, mode="today", max_attempts=3,
            retry_interval_minutes=15,
        )
        assert candidate.isoformat() == expected
        completed = archive.start_job("daily_digest", target_date=expected)
        archive.finish_job(completed, "success")

    assert pending_catchup_date(
        archive.connection, now, mode="today", max_attempts=3,
        retry_interval_minutes=15,
    ) is None
    archive.close()
