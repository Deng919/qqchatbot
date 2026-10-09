"""HTTP and periodic-task integration for the local historical gap inspector."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, StrictBool, StrictInt, field_validator

from ..archive import Archive
from ..history_inspection import HistoryInspectionService, InspectionSettings, run_history_inspection
from ..group_batch import require_existing_groups
from ..group_selection import normalize_group_selection
from .operations import OperationBusy
from .worker_threads import guarded_to_thread


class InspectionRunRequest(BaseModel):
    model_config = {'extra':'forbid'}
    refresh: StrictBool = False
    group_ids: list[StrictInt] | None = None

    @field_validator('group_ids')
    @classmethod
    def valid_scope(cls, value):
        return list(normalize_group_selection(group_ids=value,required=True))


def add_history_inspection_routes(app, *, archive, config, operations, require_login):
    def current_archive(request):
        return archive(request) if callable(archive) else archive

    def current_config(request):
        return config(request) if callable(config) else config

    def service(request):
        cfg = current_config(request)
        return HistoryInspectionService(current_archive(request), timezone_name=cfg.summary.timezone if cfg else "Asia/Shanghai")

    @app.get("/api/history-inspection")
    async def get_inspection(request: Request, page: int = Query(1, ge=1),
                             page_size: int = Query(10, ge=1, le=50),
                             group_ids: list[int] | None = Query(None)):
        require_login(request)
        try:
            ids = normalize_group_selection(group_ids=group_ids) if group_ids is not None else None
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        result = service(request).snapshot(page=page, page_size=page_size,group_ids=ids)
        result["running"] = operations.is_active("history_inspection")
        cfg = current_config(request)
        result["source_available"] = bool(cfg and cfg.ntqq.enabled and cfg.ntqq.db_dir)
        return result

    @app.put("/api/history-inspection/settings")
    async def put_settings(request: Request, payload: InspectionSettings):
        require_login(request)
        try:
            with operations.claim("history_inspection"):
                return service(request).update_settings(payload)
        except OperationBusy as exc:
            raise HTTPException(status_code=409, detail="已有任务在运行，请完成后再修改巡检设置") from exc

    @app.post("/api/history-inspection/run")
    async def post_inspection(request: Request, payload: InspectionRunRequest):
        require_login(request)
        cfg = current_config(request)
        if not cfg or not cfg.ntqq.enabled or not cfg.ntqq.db_dir:
            raise HTTPException(status_code=400, detail="请先启用 NTQQ 并配置源数据库目录")
        try:
            with operations.claim("history_inspection"):
                ids = None
                if payload.group_ids is not None:
                    try: ids = require_existing_groups(current_archive(request),payload.group_ids)
                    except KeyError as exc: raise HTTPException(404,str(exc)) from exc
                return await guarded_to_thread(run_history_inspection,config=cfg,refresh=payload.refresh,group_ids=ids)
        except OperationBusy as exc:
            raise HTTPException(status_code=409, detail="已有冲突任务在运行，请稍后巡检") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"巡检未完成：{exc}") from exc


async def run_scheduled_inspection(config, operations, *, enabled=lambda: True) -> bool:
    if not enabled() or not config or not config.ntqq.enabled or not config.ntqq.db_dir:
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
    return await guarded_to_thread(worker)


async def history_inspection_loop(config, operations, *, enabled=lambda: True):
    await asyncio.sleep(30)
    while True:
        try:
            await run_scheduled_inspection(config, operations, enabled=enabled)
        except Exception:
            logging.getLogger("qq_digest.history_inspection").exception("历史巡检失败，将在后续周期重试")
        await asyncio.sleep(60)
