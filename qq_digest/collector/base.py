from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from ..models import NormalizedMessage


class Collector(ABC):
    @abstractmethod
    def discover_groups(self) -> list[dict]:
        """返回 group_id、name、latest_message_at、message_count_30d。"""

    @abstractmethod
    def collect(
        self, group_id: int, start: datetime, end: datetime
    ) -> Iterable[NormalizedMessage]:
        """按时间窗读取消息。实现不得修改原始 QQ 数据。"""
