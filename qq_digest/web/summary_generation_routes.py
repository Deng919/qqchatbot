"""Authenticated, bounded summary input preview; never calls collection or AI."""
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ..manual_summary import ManualSummaryRequest
from ..summary_generation import preview_summary


class PreviewPayload(BaseModel):
    group_ids: list[Annotated[int, Field(strict=True, gt=0, le=2**63-1)]] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date


def add_summary_generation_routes(app, *, archive, config, require_login):
    @app.post('/api/reports/range/preview', dependencies=[Depends(require_login)])
    async def preview(payload: PreviewPayload):
        try:
            request = ManualSummaryRequest(payload.group_ids, payload.start_date, payload.end_date)
            return preview_summary(archive, request, config.summary.timezone if config else 'Asia/Shanghai')
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
