from datetime import datetime
from zoneinfo import ZoneInfo

from qq_digest.scheduler import summary_window


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
