"""Authenticated local topic tracking and correction APIs."""
from __future__ import annotations

import asyncio
from threading import Lock
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..topic_tracking import TopicConflict, TopicTrackingService
from ..archive import Archive
from .operations import OperationBusy

EntityId = Annotated[int, Path(ge=1, lt=2**63)]
Revision = Annotated[int, Field(ge=0, strict=True)]
TargetId = Annotated[int, Field(ge=1, lt=2**63, strict=True)]


class TopicUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: Revision
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal['tracking', 'archived'] | None = None

    @field_validator('title')
    @classmethod
    def title_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError('请填写话题名称')
        return value.strip() if value is not None else None


class TopicMove(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: Revision
    target_topic_id: TargetId | None = None
    new_title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator('new_title')
    @classmethod
    def title_not_blank(cls, value):
        return TopicUpdate.title_not_blank(value)


class TopicMerge(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: Revision
    target_topic_id: TargetId
    target_revision: Revision


def add_topic_routes(app, *, archive, config, templates, require_login, operations):
    # The application's connection is also used by settings and candidate edits.
    # Its commit/rollback must never publish or undo a worker's transaction.
    database_path = archive.connection.execute('PRAGMA database_list').fetchone()['file']
    worker_archive = Archive.open(FilePath(database_path)) if database_path else archive
    service = TopicTrackingService(worker_archive, timezone_name=(
        config.summary.timezone if config else 'Asia/Shanghai'))
    app.state.topic_tracking = service
    request_lock = Lock()

    def close():
        with request_lock:
            if worker_archive is not archive:
                worker_archive.close()
    app.state.close_topic_tracking = close

    async def execute(action, *, mutate=False):
        def invoke():
            # Coordinate report/file publication as well as topic corrections.
            with request_lock, operations.claim('topic_mutation'):
                return action()
        try:
            # A private in-memory database cannot be reopened. Run these small
            # test/embedded archives synchronously so the event loop cannot
            # interleave another request on their connection.
            return await asyncio.to_thread(invoke) if database_path else invoke()
        except (TopicConflict, OperationBusy) as exc:
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, '话题处理失败，请检查存储位置后重试') from exc

    @app.get('/topics', dependencies=[Depends(require_login)])
    async def topics_page(request: Request):
        return templates.TemplateResponse(request, 'topics.html', {})

    @app.get('/api/topics', dependencies=[Depends(require_login)])
    async def list_topics(q: str = Query('', max_length=100),
                          group_id: int | None = Query(None, ge=-(2**63), lt=2**63),
                          date_from: str | None = None, date_to: str | None = None,
                          status: Literal['all', 'tracking', 'archived'] = 'all',
                          page: int = Query(1, ge=1, le=2**31),
                          page_size: int = Query(20, ge=1, le=100)):
        return await execute(lambda: service.list_topics(q=q, group_id=group_id,
            date_from=date_from, date_to=date_to, status=status, page=page, page_size=page_size))

    @app.post('/api/topics/refresh', dependencies=[Depends(require_login)])
    async def refresh_topics():
        return await execute(service.refresh, mutate=True)

    @app.get('/api/topics/{topic_id}', dependencies=[Depends(require_login)])
    async def topic_detail(topic_id: EntityId, page: int = Query(1, ge=1, le=2**31),
                           page_size: int = Query(20, ge=1, le=100)):
        return await execute(lambda: service.detail(topic_id, page=page, page_size=page_size))

    @app.patch('/api/topics/{topic_id}', dependencies=[Depends(require_login)])
    async def update_topic(topic_id: EntityId, payload: TopicUpdate):
        return await execute(lambda: service.update(topic_id, **payload.model_dump()), mutate=True)

    @app.post('/api/topics/{topic_id}/merge', dependencies=[Depends(require_login)])
    async def merge_topic(topic_id: EntityId, payload: TopicMerge):
        return await execute(lambda: service.merge(topic_id, **payload.model_dump()), mutate=True)

    @app.patch('/api/topic-discussions/{discussion_id}', dependencies=[Depends(require_login)])
    async def move_topic_discussion(discussion_id: EntityId, payload: TopicMove):
        return await execute(lambda: service.move(discussion_id, **payload.model_dump()), mutate=True)

    @app.get('/api/topic-discussions/{discussion_id}/suggestions', dependencies=[Depends(require_login)])
    async def topic_suggestions(discussion_id: EntityId):
        return await execute(lambda: service.suggestions(discussion_id))

    @app.get('/api/topic-discussions/{discussion_id}/sources/{msg_id}', dependencies=[Depends(require_login)])
    async def topic_source(discussion_id: EntityId, msg_id: str,
                           direction: Literal['around', 'before', 'after'] = 'around',
                           cursor: str | None = Query(None, max_length=512)):
        return await execute(lambda: service.source(discussion_id, msg_id,
            direction=direction, cursor=cursor))
