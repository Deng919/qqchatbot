"""Authenticated report history, correction and regeneration endpoints."""
import asyncio
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..report_revisions import ReportRevisionService, RevisionConflict
from ..report_regeneration import regenerate_report
from .operations import OperationBusy


class RegenerationRequest(BaseModel):
    expected_version: int = Field(ge=0,strict=True)
    reason: str = Field(min_length=1,max_length=2000)

    @field_validator('reason')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():
            raise ValueError('请填写理由')
        return value.strip()


class CorrectionRequest(RegenerationRequest):
    category: Literal['missing','error','noise']
    excerpt: str = Field(default='',max_length=4000)
    correction: str = Field(min_length=1,max_length=4000)

    @field_validator('correction')
    @classmethod
    def nonblank_correction(cls,value):
        if not value.strip():
            raise ValueError('请填写修正意见')
        return value.strip()


class CorrectionStatus(BaseModel):
    status: Literal['open','resolved']


def add_report_revision_routes(app, *, archive, config, operations, require_login):
    service = ReportRevisionService(archive)
    prefix = '/api/reports/{kind}/{report_id}'

    def handle_error(exc):
        if isinstance(exc,(OperationBusy,RevisionConflict)):
            raise HTTPException(409,detail=str(exc) if isinstance(exc,RevisionConflict) else '已有任务运行，请稍后操作') from exc
        if isinstance(exc,LookupError):
            raise HTTPException(404,detail=str(exc)) from exc
        if isinstance(exc,ValueError):
            raise HTTPException(422,detail=str(exc)) from exc
        raise HTTPException(503,detail=f'操作未完成，当前报告保留：{str(exc)[:300]}') from exc

    @app.get(prefix+'/versions')
    async def versions(request:Request,kind:str,report_id:int,page:int=Query(1,ge=1)):
        require_login(request)
        try:
            return service.versions(kind,report_id,page=page)
        except Exception as exc:
            handle_error(exc)

    @app.get(prefix+'/versions/{version}')
    async def version(request:Request,kind:str,report_id:int,version:int):
        require_login(request)
        try:
            return service.version(kind,report_id,version)
        except Exception as exc:
            handle_error(exc)

    @app.get(prefix+'/corrections')
    async def corrections(request:Request,kind:str,report_id:int):
        require_login(request)
        try:
            return {'corrections':service.corrections(kind,report_id)}
        except Exception as exc:
            handle_error(exc)

    @app.post(prefix+'/corrections', dependencies=[Depends(require_login)])
    async def add_correction(request:Request,kind:str,report_id:int,payload:CorrectionRequest):
        require_login(request)
        try:
            with operations.claim('manual_summary'):
                return service.add_correction(kind,report_id,**payload.model_dump())
        except Exception as exc:
            handle_error(exc)

    @app.put(prefix+'/corrections/{correction_id}', dependencies=[Depends(require_login)])
    async def correction_status(request:Request,kind:str,report_id:int,correction_id:int,payload:CorrectionStatus):
        require_login(request)
        try:
            with operations.claim('manual_summary'):
                return service.set_correction_status(kind,report_id,correction_id,payload.status)
        except Exception as exc:
            handle_error(exc)

    @app.post(prefix+'/regenerate', dependencies=[Depends(require_login)])
    async def regenerate(request:Request,kind:str,report_id:int,payload:RegenerationRequest):
        require_login(request)
        if config is None:
            raise HTTPException(503,detail='AI 配置不可用')
        def worker():
            with operations.claim('manual_summary'):
                return regenerate_report(config=config,kind=kind,report_id=report_id,**payload.model_dump())
        try:
            return await asyncio.to_thread(worker)
        except Exception as exc:
            handle_error(exc)
