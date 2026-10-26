"""Core summary reading routes, independent of optional catch-up navigation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, Field, StrictBool

from ..catchup import CatchupService


class SummaryReadPayload(BaseModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    read: StrictBool


class SummaryVisitPayload(BaseModel):
    viewed_at: AwareDatetime | None = None


class CatchupReadPayload(BaseModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    read: bool


def add_summary_reading_routes(app, *, archive, config, require_login):
    def service(source=archive):
        return CatchupService(source, timezone_name=(
            config.summary.timezone if config is not None else "Asia/Shanghai"
        ))

    def read_items(scope, **kwargs):
        with archive.read_snapshot() as reader:
            return service(reader).list_items(scope, **kwargs)

    @app.get("/api/summary-reading", dependencies=[Depends(require_login)])
    async def summary_reading(
        date_from: str | None = None, date_to: str | None = None,
        group_id: int | None = None,
        group_ids: list[int] | None = Query(None),
        read_filter: Literal["all", "unread", "new"] = "all",
        since: str | None = None, page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        report_keys: list[str] | None = Query(None),
        grouped: bool = False, all_dates: bool = False,
    ):
        try:
            return await asyncio.to_thread(
                read_items, "all" if all_dates else "custom", date_from=date_from, date_to=date_to,
                group_id=group_id, read_filter=read_filter, since=since,
                page=page, page_size=page_size,
                include_ranges=True,group_ids=group_ids,
                report_keys=report_keys,grouped=grouped,
            )
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.get("/api/summary-reading/visit", dependencies=[Depends(require_login)])
    async def visit_baseline():
        def read():
            with archive.read_snapshot() as reader:
                return service(reader).last_visit()
        return await asyncio.to_thread(read)

    @app.post("/api/summary-reading/visit", dependencies=[Depends(require_login)])
    async def visit(payload: SummaryVisitPayload | None = None):
        opened = payload.viewed_at if payload else None
        if opened and opened > datetime.now(timezone.utc):
            raise HTTPException(422, detail="打开时间不能晚于当前时间")
        return service().visit(now=opened)

    @app.post("/api/summary-reading/read", dependencies=[Depends(require_login)])
    async def read(payload: SummaryReadPayload):
        service().set_read(payload.key, payload.read)
        return {"key": payload.key, "read": payload.read}
    # Compatibility APIs use the same reading state and source identities.
    @app.post("/api/catchup/visit")
    async def api_catchup_visit(request: Request):
        require_login(request)
        return service().visit()

    @app.get("/api/catchup")
    async def api_catchup(
        request: Request, scope: Literal["since", "today", "week"] = "since",
        since: str | None = None, page: int = Query(1, ge=1),
    ):
        require_login(request)
        try:
            return await asyncio.to_thread(
                read_items, scope, since=since, page=page
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/catchup/read")
    async def api_catchup_read(request: Request, payload: CatchupReadPayload):
        require_login(request)
        service().set_read(payload.key, payload.read)
        return {"key": payload.key, "read": payload.read}
