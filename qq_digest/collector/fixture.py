from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import NormalizedMessage
from .base import Collector


class FixtureCollector(Collector):
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai"))

    def discover_groups(self):
        return [
            {
                "group_id": 123,
                "name": "测试群",
                "latest_message_at": self.now,
                "message_count_30d": 1,
            }
        ]

    def collect(self, group_id, start, end):
        timestamp = min(self.now, end)
        if start <= timestamp <= end:
            yield NormalizedMessage(
                msg_id=f"fixture-{group_id}",
                group_id=group_id,
                sender_qq=10001,
                timestamp=timestamp,
                text="https://example.com 是高质量教程站点",
                collected_at=self.now,
            )
