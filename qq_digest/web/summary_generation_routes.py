"""Authenticated, bounded summary input preview; never calls collection or AI."""
from datetime import date
import asyncio
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ..manual_summary import ManualSummaryRequest
from ..summary_generation import preview_summary
from ..summary_progress import SummaryProgress, ProgressExists
from .operations import OperationBusy


class PreviewPayload(BaseModel):
    group_ids: list[Annotated[int, Field(strict=True, gt=0, le=2**63-1)]] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date


class GenerationPayload(PreviewPayload):
    generation_id: UUID | None = None


def add_summary_generation_routes(app, *, archive, config, require_login, run_summary, operations):
    progress = SummaryProgress()
    workers = set()

    def worker_done(task):
        workers.discard(task)
        if not task.cancelled():
            task.exception()  # Consume failures even if the requesting page disconnected.

    @app.get('/api/summaries/progress', dependencies=[Depends(require_login)])
    async def generation_progress(generation_id: UUID | None = None):
        snapshot = progress.snapshot(str(generation_id) if generation_id else None)
        if generation_id and snapshot is None:
            raise HTTPException(404, detail='本次进度已不存在，程序可能已重启；请检查已有摘要和运行记录')
        return {'generation': snapshot}

    @app.post('/api/reports/range/preview', dependencies=[Depends(require_login)])
    async def preview(payload: PreviewPayload):
        try:
            request = ManualSummaryRequest(payload.group_ids, payload.start_date, payload.end_date)
            return preview_summary(archive, request, config.summary.timezone if config else 'Asia/Shanghai')
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post('/api/summaries', dependencies=[Depends(require_login)])
    async def generate(payload: GenerationPayload):
        if config is None:
            raise HTTPException(400,detail='未加载配置，无法生成摘要')
        try:
            ManualSummaryRequest(payload.group_ids,payload.start_date,payload.end_date)
            claim = operations.claim('manual_summary')
            claim.__enter__()
            identifier = str(payload.generation_id or uuid4())
            try:
                progress.start(identifier, payload.model_dump(mode='json', exclude={'generation_id'}))
            except BaseException:
                claim.__exit__(None, None, None)
                raise

            async def worker():
                try:
                    result = await asyncio.to_thread(
                        run_summary, config, payload, single_day_as_daily=True,
                        on_progress=lambda **event: progress.update(identifier, **event),
                    )
                    progress.finish(identifier, result=result)
                    return result
                except Exception:
                    progress.finish(identifier, error='生成中断，请查看运行记录并检查 AI 连接')
                    raise
                finally:
                    # The worker owns the lock, independent of request cancellations.
                    claim.__exit__(None, None, None)

            task = asyncio.create_task(worker())
            workers.add(task)
            task.add_done_callback(worker_done)
            return await asyncio.shield(task)
        except OperationBusy as exc:
            raise HTTPException(409,detail={'message':'已有摘要或消息更新任务在运行','active':exc.active}) from exc
        except ProgressExists as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,detail=str(exc)) from exc
