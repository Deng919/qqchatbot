"""Authenticated bookmark API and page."""
from typing import Annotated, Literal
from contextlib import nullcontext

from fastapi import Depends, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..bookmarks import BookmarkConflict, BookmarkService
from ..operations import OperationBusy

PositiveId = Annotated[int, Field(ge=1, lt=2**63, strict=True)]
EntityId = Annotated[int, Path(ge=1, lt=2**63)]


class MessageBookmark(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['message']
    group_id: Annotated[int, Field(ge=-(2**63), lt=2**63, strict=True)]
    msg_id: str = Field(min_length=1, max_length=512)


class ReportBookmark(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['report']
    report_kind: Literal['daily', 'range']
    report_id: PositiveId
    point_key: str = Field(pattern=r'^[0-9a-f]{64}$')


class CandidateBookmark(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['resource', 'knowledge']
    candidate_id: PositiveId


BookmarkTarget = Annotated[MessageBookmark | ReportBookmark | CandidateBookmark, Field(discriminator='kind')]


class BookmarkUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    status: Literal['pending', 'completed']
    expected_revision: int = Field(ge=0, lt=2**63, strict=True)


def add_bookmark_routes(app, *, archive, config, templates, require_login, operations):
    service = BookmarkService(archive, config.summary.timezone if config else 'Asia/Shanghai')
    app.state.bookmarks = service

    def execute(action, *, mutate=False):
        try:
            with operations.claim('bookmark_mutation') if mutate else nullcontext():
                return action()
        except (BookmarkConflict, OperationBusy) as exc: raise HTTPException(409, str(exc)) from exc
        except LookupError as exc: raise HTTPException(404, str(exc)) from exc
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
        except OSError as exc: raise HTTPException(503, '收藏保存失败，请检查存储位置后重试') from exc

    @app.get('/bookmarks')
    async def page(request: Request):
        try: require_login(request)
        except HTTPException: return RedirectResponse('/login', status_code=303)
        return templates.TemplateResponse(request, 'bookmarks.html', {})

    @app.get('/api/bookmarks', dependencies=[Depends(require_login)])
    async def list_items(status: Literal['pending', 'completed', 'all'] = 'pending',
                         kind: Literal['message', 'report', 'resource', 'knowledge'] | None = None,
                         q: str = Query('', max_length=100), page: int = Query(1, ge=1, le=2**31)):
        return execute(lambda: service.list_items(status=status, kind=kind, q=q, page=page))

    @app.post('/api/bookmarks', dependencies=[Depends(require_login)])
    async def save(payload: BookmarkTarget):
        return execute(lambda: service.save(payload.model_dump()), mutate=True)

    @app.get('/api/bookmarks/{bookmark_id}', dependencies=[Depends(require_login)])
    async def detail(bookmark_id: EntityId):
        return execute(lambda: service.detail(bookmark_id))

    @app.patch('/api/bookmarks/{bookmark_id}', dependencies=[Depends(require_login)])
    async def update(bookmark_id: EntityId, payload: BookmarkUpdate):
        return execute(lambda: service.update(bookmark_id, **payload.model_dump()), mutate=True)

    @app.delete('/api/bookmarks/{bookmark_id}', dependencies=[Depends(require_login)])
    async def remove(bookmark_id: EntityId, expected_revision: int = Query(ge=0, lt=2**63)):
        return execute(lambda: service.remove(bookmark_id, expected_revision), mutate=True)

    @app.get('/api/bookmarks/{bookmark_id}/context', dependencies=[Depends(require_login)])
    async def context(bookmark_id: EntityId, direction: Literal['around', 'before', 'after'] = 'around',
                      cursor: str | None = Query(None, max_length=512)):
        return execute(lambda: service.context(bookmark_id, direction, cursor))
