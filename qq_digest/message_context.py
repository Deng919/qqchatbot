"""Read same-group message context with stable directional cursors."""

from __future__ import annotations

from .archive import Archive


def message_context(archive: Archive, group_id: int, anchor_id: str, *,
                    direction: str = 'around', cursor: str | None = None) -> dict:
    if direction not in {'around', 'before', 'after'}:
        raise ValueError('上下文方向必须是 around、before 或 after')
    if direction != 'around' and not cursor:
        raise ValueError('继续查看上下文需要消息游标')
    if not -(2**63) <= group_id < 2**63:
        raise ValueError('群号超出有效范围')
    connection = archive.connection

    def lookup(msg_id):
        row = connection.execute(
            'SELECT msg_id,sender_qq,timestamp,text FROM messages WHERE group_id=? AND msg_id=?',
            (group_id, msg_id),
        ).fetchone()
        if row is None:
            raise LookupError('原消息或上下文游标不存在于当前群')
        return row

    def adjacent(row, side, limit):
        operator, order = ('<', 'DESC') if side == 'before' else ('>', 'ASC')
        rows = connection.execute(
            'SELECT msg_id,sender_qq,timestamp,text FROM messages WHERE group_id=? '
            f'AND (timestamp {operator} ? OR (timestamp=? AND msg_id {operator} ?)) '
            f'ORDER BY timestamp {order},msg_id {order} LIMIT ?',
            (group_id, row['timestamp'], row['timestamp'], row['msg_id'], limit),
        ).fetchall()
        return list(reversed(rows)) if side == 'before' else rows

    anchor = lookup(anchor_id)
    if direction == 'around':
        rows = [*adjacent(anchor, 'before', 2), anchor, *adjacent(anchor, 'after', 2)]
        edge = anchor
    else:
        edge = lookup(cursor)
        rows = adjacent(edge, direction, 20)
    first, last = (rows[0], rows[-1]) if rows else (edge, edge)
    return {'messages': [dict(row) for row in rows], 'first_id': first['msg_id'],
            'last_id': last['msg_id'], 'has_before': bool(adjacent(first, 'before', 1)),
            'has_after': bool(adjacent(last, 'after', 1)), 'anchor_id': anchor_id}
