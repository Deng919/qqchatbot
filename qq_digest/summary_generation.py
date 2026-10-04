"""Read-only input preview for the existing manual summary flow."""
from datetime import timezone
from zoneinfo import ZoneInfo

from .manual_summary import ManualSummaryRequest, range_window


def preview_summary(archive, request: ManualSummaryRequest, timezone_name: str) -> dict:
    enabled = {group.group_id: group for group in archive.enabled_groups()}
    if any(group_id not in enabled for group_id in request.group_ids):
        raise ValueError('所选群不存在或未关注，请重新选择')
    try:
        start, end = range_window(request, ZoneInfo(timezone_name))
        start_utc = start.astimezone(timezone.utc).isoformat()
        end_utc = end.astimezone(timezone.utc).isoformat()
    except OverflowError as exc:
        raise ValueError('日期超出支持的范围') from exc
    placeholders = ','.join('?' for _ in request.group_ids)
    counts = dict(archive.connection.execute(
        f'SELECT group_id, COUNT(*) FROM messages WHERE group_id IN ({placeholders}) '
        'AND timestamp>=? AND timestamp<? GROUP BY group_id',
        [*request.group_ids, start_utc, end_utc],
    ).fetchall())
    groups = [{'group_id':group_id, 'group_name':enabled[group_id].name,
               'message_count':counts.get(group_id, 0)} for group_id in request.group_ids]
    return {'start_date':request.start_date.isoformat(), 'end_date':request.end_date.isoformat(),
            'timezone':timezone_name, 'source':'local_archive', 'groups':groups,
            'total_messages':sum(group['message_count'] for group in groups)}
