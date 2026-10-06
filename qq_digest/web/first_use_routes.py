"""Authenticated first-use workflow. Worker owns locks even if HTTP disconnects."""
import asyncio
import sqlite3
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, Field, StrictBool

from ..features import FeatureConflict
from ..first_use import FirstUseService, SetupConflict, detect_accounts, refresh_setup_source
from ..operations import OperationBusy

GroupId = Annotated[int, Field(strict=True, gt=0, lt=2**63)]


class Version(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')


class Source(Version):
    db_dir: str = Field(min_length=1, max_length=1024)
    qq_number: int = Field(default=0, strict=True, ge=0, lt=2**63)


class Refresh(Version):
    qq_number: GroupId


class Groups(Version):
    group_ids: list[GroupId] = Field(min_length=1, max_length=100)


class Import(Version):
    group_id: GroupId
    start_date: date
    end_date: date


class AI(Version):
    provider: Literal['compatible', 'chatgpt_bridge']
    base_url: str = Field(default='', max_length=1024)
    model: str = Field(default='', max_length=200)
    api_key: str = Field(default='', max_length=4096)


class Schedule(Version):
    auto_collection: StrictBool
    auto_daily: StrictBool
    interval_minutes: int = Field(strict=True, ge=1, le=1440)
    hour: int = Field(strict=True, ge=0, le=23)
    minute: int = Field(strict=True, ge=0, le=59)
    features_revision: int = Field(strict=True, ge=0, lt=2**63)


def add_first_use_routes(app, *, config, config_path, archive, templates, require_login, operations):
    service = FirstUseService(config, config_path, archive) if config else None
    app.state.first_use = service

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith('/api/setup/'):
            return JSONResponse(status_code=422, content={'detail': '输入无效，请检查账号、日期、地址、模型和数值范围'})
        return await request_validation_exception_handler(request, exc)

    def get_service():
        if service is None:
            raise HTTPException(503, '未加载配置，请重新启动程序')
        return service

    async def mutate(action):
        def worker():
            with operations.claim('setup_mutation'):
                with get_service().store.lock:
                    previous = get_service().store.revision
                    try:
                        return action(get_service())
                    finally:
                        if get_service().store.revision != previous and hasattr(app.state, 'desktop_config_signature'):
                            from ..desktop import config_signature
                            app.state.desktop_config_signature = config_signature(config_path)
        try:
            return await asyncio.to_thread(worker)
        except (SetupConflict, FeatureConflict, OperationBusy) as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, '保存失败，请检查配置目录权限和存储空间后重试') from exc
        except sqlite3.DatabaseError as exc:
            raise HTTPException(503, '归档写入失败，请检查存储空间后重试') from exc

    @app.get('/setup')
    async def page(request: Request):
        try: require_login(request)
        except HTTPException: return RedirectResponse('/login', status_code=303)
        return templates.TemplateResponse(request, 'first_use.html', {})

    @app.get('/api/setup', dependencies=[Depends(require_login)])
    async def state():
        result = get_service().snapshot()
        result['busy'] = operations.is_active('setup_mutation')
        return result

    @app.get('/api/setup/detect', dependencies=[Depends(require_login)])
    async def detect():
        try: return {'accounts': await asyncio.to_thread(detect_accounts)}
        except OSError: raise HTTPException(503, '无法检测 QQ 目录，请使用已有解密库或手动填写账号')

    @app.get('/api/setup/groups', dependencies=[Depends(require_login)])
    async def groups():
        try: return {'groups': await asyncio.to_thread(get_service().source_groups)}
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @app.get('/api/setup/preview', dependencies=[Depends(require_login)])
    async def preview():
        try: return get_service().preview()
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @app.post('/api/setup/source', dependencies=[Depends(require_login)])
    async def source(payload: Source):
        return await mutate(lambda svc: svc.set_source(payload.expected_revision, payload.db_dir, payload.qq_number))

    @app.post('/api/setup/refresh', dependencies=[Depends(require_login)])
    async def refresh(payload: Refresh):
        def action(svc):
            svc.store.check(payload.expected_revision)
            path = refresh_setup_source(payload.qq_number, config.work_dir)
            return svc.set_source(payload.expected_revision, str(path), payload.qq_number)
        return await mutate(action)

    @app.post('/api/setup/groups', dependencies=[Depends(require_login)])
    async def choose(payload: Groups):
        return await mutate(lambda svc: svc.select_groups(payload.expected_revision, payload.group_ids))

    @app.post('/api/setup/import', dependencies=[Depends(require_login)])
    async def import_one(payload: Import):
        return await mutate(lambda svc: svc.import_group(payload.expected_revision, payload.group_id, payload.start_date, payload.end_date))

    @app.post('/api/setup/ai', dependencies=[Depends(require_login)])
    async def ai(payload: AI):
        return await mutate(lambda svc: svc.set_ai(payload.expected_revision, payload.provider, payload.base_url, payload.model, payload.api_key))

    @app.post('/api/setup/ai-test', dependencies=[Depends(require_login)])
    async def test(payload: Version):
        return await mutate(lambda svc: svc.test_ai(payload.expected_revision))

    @app.post('/api/setup/ai-skip', dependencies=[Depends(require_login)])
    async def skip(payload: Version):
        return await mutate(lambda svc: svc.skip_ai(payload.expected_revision))

    @app.post('/api/setup/schedule', dependencies=[Depends(require_login)])
    async def schedule(payload: Schedule):
        return await mutate(lambda svc: svc.set_schedule(payload.expected_revision, payload.auto_collection,
            payload.auto_daily, payload.interval_minutes, payload.hour, payload.minute, payload.features_revision))

    @app.post('/api/setup/finish', dependencies=[Depends(require_login)])
    async def finish(payload: Version):
        return await mutate(lambda svc: svc.finish(payload.expected_revision))
