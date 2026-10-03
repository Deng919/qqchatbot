"""Authenticated message archive browsing routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query

from ..message_browsing import browse_messages


def add_message_routes(app, *, archive, config, require_login):
    @app.get("/api/messages", dependencies=[Depends(require_login)])
    async def messages(
        group_id: int | None = None,
        date_from: str | None = None, date_to: str | None = None,
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    ):
        try:
            return browse_messages(
                archive, group_id=group_id, date_from=date_from, date_to=date_to,
                page=page, page_size=page_size,
                timezone_name=config.summary.timezone if config else "Asia/Shanghai",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
