"""HTTP and periodic-task integration for the local historical gap inspector."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, StrictBool

from ..archive import Archive
from ..history_inspection import HistoryInspectionService, InspectionSettings, run_history_inspection
from .operations import OperationBusy


class InspectionRunRequest(BaseModel):
    refresh: StrictBool = False


def add_history_inspection_routes(app, *, archive, config, operations, require_login):
    def service():
        return HistoryInspectionService(archive, timezone_name=config.summary.timezone if config else "Asia/Shanghai")

    @app.get("/api/history-inspection")
    async def get_inspection(request: Request, page: int = Query(1, ge=1),
                             page_size: int = Query(10, ge=1, le=50)):
        require_login(request)
        result = service().snapshot(page=page, page_size=page_size)
        result["running"] = operations.is_active("history_inspection")
        result["source_available"] = bool(config and config.ntqq.enabled and config.ntqq.db_dir)
        return result

    @app.put("/api/history-inspection/settings")
    async def put_settings(request: Request, payload: InspectionSettings):
        require_login(request)
        try:
            with operations.claim("history_inspection"):
                return service().update_settings(payload)
        except OperationBusy as exc:
            raise HTTPException(status_code=409, detail="已有任务在运行，请完成后再修改巡检设置") from exc

    @app.post("/api/history-inspection/run")
    async def post_inspection(request: Request, payload: InspectionRunRequest):
        require_login(request)
        if not config or not config.ntqq.enabled or not config.ntqq.db_dir:
            raise HTTPException(status_code=400, detail="请先启用 NTQQ 并配置源数据库目录")
        def worker():
            # Keep the claim in the worker so cancellation of the HTTP request
            # cannot release it while the actual source read is still running.
            with operations.claim("history_inspection"):
                return run_history_inspection(config=config, refresh=payload.refresh)
        try:
            return await asyncio.to_thread(worker)
        except OperationBusy as exc:
            raise HTTPException(status_code=409, detail="已有冲突任务在运行，请稍后巡检") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"巡检未完成：{exc}") from exc


async def run_scheduled_inspection(config, operations) -> bool:
    if not config or not config.ntqq.enabled or not config.ntqq.db_dir:
        return False
    def worker():
        try:
            with operations.claim("history_inspection"):
                archive = Archive.open(config.archive_path)
                try:
                    due = HistoryInspectionService(archive).due(datetime.now(timezone.utc))
                    has_groups = bool(archive.enabled_groups())
                finally:
                    archive.close()
                if not due or not has_groups:
                    return False
                run_history_inspection(config=config, refresh=False)
                return True
        except OperationBusy:
            return False
    return await asyncio.to_thread(worker)


async def history_inspection_loop(config, operations):
    await asyncio.sleep(30)
    while True:
        try:
            await run_scheduled_inspection(config, operations)
        except Exception:
            logging.getLogger("qq_digest.history_inspection").exception("历史巡检失败，将在后续周期重试")
        await asyncio.sleep(60)
