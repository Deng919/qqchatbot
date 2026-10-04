"""Authenticated, bounded summary input preview; never calls collection or AI."""
from datetime import date
import asyncio
from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ..manual_summary import ManualSummaryRequest
from ..summary_generation import preview_summary
from .operations import OperationBusy


class PreviewPayload(BaseModel):
    group_ids: list[Annotated[int, Field(strict=True, gt=0, le=2**63-1)]] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date


def add_summary_generation_routes(app, *, archive, config, require_login, run_summary, operations):
    @app.post('/api/reports/range/preview', dependencies=[Depends(require_login)])
    async def preview(payload: PreviewPayload):
        try:
            request = ManualSummaryRequest(payload.group_ids, payload.start_date, payload.end_date)
            return preview_summary(archive, request, config.summary.timezone if config else 'Asia/Shanghai')
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post('/api/summaries', dependencies=[Depends(require_login)])
    async def generate(payload: PreviewPayload):
        if config is None:
            raise HTTPException(400,detail='未加载配置，无法生成摘要')
        try:
            ManualSummaryRequest(payload.group_ids,payload.start_date,payload.end_date)
            with operations.claim('manual_summary'):
                return await asyncio.to_thread(run_summary,config,payload,single_day_as_daily=True)
        except OperationBusy as exc:
            raise HTTPException(409,detail={'message':'已有摘要或消息更新任务在运行','active':exc.active}) from exc
        except ValueError as exc:
            raise HTTPException(422,detail=str(exc)) from exc
