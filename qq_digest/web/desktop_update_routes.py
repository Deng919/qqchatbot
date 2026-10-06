"""Authenticated local desktop version controls."""
import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ..operations import OperationBusy


class ReleaseAction(BaseModel):
    model_config = {'extra': 'forbid'}
    version: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')


def add_desktop_update_routes(app, *, templates, require_login, desktop_bridge, desktop_mutation):
    @app.get('/desktop-updates')
    async def page(request: Request):
        require_login(request)
        return templates.TemplateResponse(request, 'desktop_updates.html', {})

    @app.get('/api/desktop-updates')
    async def versions(request: Request):
        bridge = desktop_bridge(request)
        try:
            return await asyncio.to_thread(bridge.get_updates)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    async def mutate(request, method, *args):
        bridge = desktop_bridge(request)
        try:
            return await desktop_mutation(request, bridge, 'version_mutation', method, *args)
        except (ValueError, OSError, RuntimeError) as exc:
            if isinstance(exc, OperationBusy):
                raise HTTPException(409, detail='有任务正在运行，请稍后重试') from exc
            raise HTTPException(400, detail=str(exc)) from exc

    @app.post('/api/desktop-updates/switch')
    async def switch(request: Request, payload: ReleaseAction):
        return await mutate(request, 'switch_version', payload.version, payload.digest)

    @app.post('/api/desktop-updates/clean')
    async def clean(request: Request, payload: ReleaseAction):
        return await mutate(request, 'clean_version', payload.version, payload.digest)

    @app.post('/api/desktop-updates/recover')
    async def recover(request: Request):
        return await mutate(request, 'recover_version')
