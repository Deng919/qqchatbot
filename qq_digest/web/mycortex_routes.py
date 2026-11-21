"""Authenticated interface used by every QQ Digest page that saves to MyCortex."""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..ai.client import AIError, AILimitExceeded
from ..archive import Archive
from ..config import ConfigError
from ..mycortex import MAX_MESSAGES, MyCortexDelivery, SourceChanged
from ..mycortex_transport import MyCortexUnavailable
from ..operations import OperationBusy
from .worker_threads import guarded_to_thread


GroupId = Annotated[int, Path(ge=-(2**63), lt=2**63)]
MessageId = Annotated[str, StringConstraints(min_length=1, max_length=512, strict=True)]


class MessageSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    group_id: int = Field(ge=-(2**63), lt=2**63)
    message_ids: list[MessageId] = Field(min_length=1, max_length=MAX_MESSAGES)


class AutomaticSetting(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


def configured(config) -> bool:
    return bool(getattr(getattr(config, 'mycortex', None), 'configured', False))


def add_mycortex_routes(app, *, require_login, operations):
    async def execute(request, action, *args, mutate=True):
        def worker():
            config = request.app.state.config
            if not configured(config):
                raise MyCortexUnavailable('请先配置 MyCortex 本机连接')
            archive = Archive.open(config.archive_path)
            try:
                return getattr(MyCortexDelivery(archive, config), action)(*args)
            finally:
                archive.close()

        try:
            if mutate:
                with operations.claim('mycortex'):
                    return await guarded_to_thread(worker)
            # A settings read must not wait for a model call or mutate the archive.
            config = request.app.state.config
            if not configured(config):
                raise MyCortexUnavailable('请先配置 MyCortex 本机连接')
            with request.app.state.archive.read_snapshot() as archive:
                return getattr(MyCortexDelivery(archive, config), action)(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (OperationBusy, SourceChanged) as exc:
            raise HTTPException(409, str(exc)) from exc
        except AILimitExceeded as exc:
            raise HTTPException(429, str(exc)) from exc
        except MyCortexUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except (AIError, ConfigError) as exc:
            raise HTTPException(503, 'Cockpit 提炼暂时不可用，请检查 AI 设置后重试') from exc
        except sqlite3.Error as exc:
            raise HTTPException(503, 'QQ Digest 归档暂时不可用，请重试') from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/mycortex/v1/submissions', dependencies=[Depends(require_login)])
    async def submit(request: Request, payload: MessageSubmission):
        return await execute(request, 'submit', payload.group_id, payload.message_ids)

    @app.get('/api/mycortex/v1/groups/{group_id}/auto', dependencies=[Depends(require_login)])
    async def get_automatic(request: Request, group_id: GroupId):
        return await execute(request, 'automatic_state', group_id, mutate=False)

    @app.put('/api/mycortex/v1/groups/{group_id}/auto', dependencies=[Depends(require_login)])
    async def set_automatic(request: Request, group_id: GroupId, payload: AutomaticSetting):
        return await execute(request, 'set_automatic', group_id, payload.enabled)
