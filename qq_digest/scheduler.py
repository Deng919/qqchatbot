from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


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
