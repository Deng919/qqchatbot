"""Authenticated desktop storage inventory and retention controls."""
import asyncio
import sqlite3

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from ..operations import OperationBusy
from ..storage_files import StorageBusy
from ..storage_policy import StorageConflict


class RetentionSettings(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool
    backup_days: StrictInt = Field(ge=1, le=3650)
    backup_keep: StrictInt = Field(ge=1, le=100)
    cache_days: StrictInt = Field(ge=1, le=3650)
    expected_revision: StrictInt = Field(ge=0, le=2**63-1)


class CleanupRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    token: StrictStr = Field(pattern=r'^[0-9a-f]{32}$')


def add_storage_routes(app, *, require_login, desktop_bridge, desktop_mutation):
    async def execute(request, method, *args):
        bridge = desktop_bridge(request)
        try:
            if method == 'get_storage': return await asyncio.to_thread(bridge.get_storage)
            return await desktop_mutation(request, bridge, 'storage_mutation', method, *args)
        except (StorageConflict, StorageBusy, OperationBusy) as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, '无法读取或清理存储，请检查目录权限后重试') from exc

    @app.get('/api/storage', dependencies=[Depends(require_login)])
    async def get(request: Request):
        return await execute(request, 'get_storage')

    @app.put('/api/storage/policy', dependencies=[Depends(require_login)])
    async def put(request: Request, payload: RetentionSettings):
        values = payload.model_dump(exclude={'expected_revision'})
        return await execute(request, 'update_storage_policy', values, payload.expected_revision)

    @app.post('/api/storage/preview', dependencies=[Depends(require_login)])
    async def preview(request: Request):
        return await execute(request, 'preview_storage_cleanup')

    @app.post('/api/storage/clean', dependencies=[Depends(require_login)])
    async def clean(request: Request, payload: CleanupRequest):
        return await execute(request, 'clean_storage', payload.token)
