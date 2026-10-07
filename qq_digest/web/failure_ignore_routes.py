"""Reversible dismissal of explicitly selected operational failure snapshots."""
from fastapi import Depends
from pydantic import BaseModel, Field
from starlette.requests import Request

from ..failure_center import FailureCenterService


class FailureSelection(BaseModel):
    id: str = Field(min_length=1, max_length=150, strict=True)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)


class FailureSelections(BaseModel):
    items: list[FailureSelection] = Field(min_length=1, max_length=400)


def add_failure_ignore_routes(app, *, archive, require_login):
    @app.post('/api/failures/ignore', dependencies=[Depends(require_login)])
    async def ignore(request: Request, payload: FailureSelections):
        return FailureCenterService(archive(request)).ignore_items([item.model_dump() for item in payload.items])

    @app.post('/api/failures/restore', dependencies=[Depends(require_login)])
    async def restore(request: Request, payload: FailureSelections):
        return FailureCenterService(archive(request)).restore_items([item.model_dump() for item in payload.items])
