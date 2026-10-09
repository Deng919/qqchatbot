"""Authenticated usage settings and monthly API statistics."""
import asyncio
import sqlite3

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictInt, StrictStr

from ..ai_usage import UsageConflict, usage_for, provider_key
from ..operations import OperationBusy


class ModelRate(BaseModel):
    model_config={'extra':'forbid'}
    provider: StrictStr=Field(max_length=300)
    model: StrictStr=Field(min_length=1,max_length=100)
    input_hit: StrictStr | None=Field(default=None,max_length=14)
    input_miss: StrictStr | None=Field(default=None,max_length=14)
    output: StrictStr | None=Field(default=None,max_length=14)


class UsageSettings(BaseModel):
    model_config={'extra':'forbid'}
    monthly_limit: StrictInt=Field(ge=0,le=10**9)
    rates: list[ModelRate]=Field(max_length=100)
    expected_revision: StrictInt=Field(ge=0,le=2**63-1)


def add_ai_usage_routes(app,*,config,require_login,operations):
    def snapshot(request):
        cfg=config(request)
        if cfg is None:raise HTTPException(503,'AI 配置不可用')
        result=usage_for(cfg).snapshot(read_only=True)
        try:origin=provider_key(cfg.ai.base_url)
        except ValueError:origin=None
        result['current_model']={'provider':origin,'model':cfg.ai.model}
        return result

    async def execute(action):
        try:return await asyncio.to_thread(action)
        except (UsageConflict,OperationBusy) as exc:raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except (sqlite3.Error,OSError) as exc:raise HTTPException(503,'无法读取或保存 AI 用量，请检查存储后重试') from exc

    @app.get('/api/ai-usage',dependencies=[Depends(require_login)])
    async def get(request:Request):return await execute(lambda:snapshot(request))

    @app.put('/api/ai-usage/settings',dependencies=[Depends(require_login)])
    async def put(request:Request,payload:UsageSettings):
        def save():
            with operations.claim('group_mutation'):
                cfg=config(request)
                if cfg is None:raise HTTPException(503,'AI 配置不可用')
                usage_for(cfg).update(payload.monthly_limit,[rate.model_dump() for rate in payload.rates],payload.expected_revision)
                return snapshot(request)
        return await execute(save)
