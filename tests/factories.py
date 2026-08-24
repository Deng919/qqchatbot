from datetime import datetime
from zoneinfo import ZoneInfo

from qq_digest.models import NormalizedMessage


def make_message(
    msg_id="m1",
    timestamp=datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
):
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=123,
        sender_qq=10001,
        timestamp=timestamp,
        text="消息",
        collected_at=datetime(2026, 8, 24, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
