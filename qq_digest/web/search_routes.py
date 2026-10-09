"""Authenticated search; message-only filters cannot silently widen scope."""
from datetime import date
from typing import Literal

from fastapi import Depends, HTTPException, Query

from ..search import search_archive


def add_search_routes(app, *, archive, config, require_login):
    @app.get('/api/search', dependencies=[Depends(require_login)])
    async def search(
        q: str = '', kind: Literal['all', 'message', 'report', 'knowledge'] = 'all',
        group_id: int | None = Query(None, ge=-(2**63), lt=2**63),
        group_ids: list[int] | None = Query(None),
        date_from: date | None = None, date_to: date | None = None,
        sender_qq: int | None = Query(None, ge=1, lt=2**63),
        message_type: Literal['text', 'link', 'file', 'reply', 'forward', 'system', 'unknown'] | None = None,
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    ):
        try:
            return search_archive(archive, query=q, kind=kind, group_id=group_id, group_ids=group_ids,
                date_from=date_from, date_to=date_to, sender_qq=sender_qq, message_type=message_type,
                page=page, page_size=page_size,
                timezone_name=config.summary.timezone if config else 'Asia/Shanghai')
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
