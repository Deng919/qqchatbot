"""Authenticated settings API and live routing guards for optional features."""
from __future__ import annotations

import re
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, StrictBool

from ..features import FeatureConflict, FeatureService


class FeatureUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    values: dict[str, StrictBool]
    expected_revision: int = Field(ge=0, strict=True)


def path_feature(path):
    for key, prefix in (('catchup', 'catchup'), ('tasks', 'tasks'), ('failures', 'failures'),
                        ('review', 'candidates'), ('history_inspection', 'history-inspection')):
        if any(path == root or path.startswith(root + '/') for root in ('/' + prefix, '/api/' + prefix)):
            return key
    if re.fullmatch(r'/api/reports/(daily|range)/[^/]+/ask', path):
        return 'report_qa'
    if re.fullmatch(r'/api/reports/(daily|range)/[^/]+/(versions(?:/[^/]+)?|corrections(?:/[^/]+)?|regenerate)', path):
        return 'report_revisions'
    return None


def add_feature_routes(app, *, archive, cookie, require_login):
    service = FeatureService(archive)
    app.state.features = service

    @app.middleware('http')
    async def feature_guard(request, call_next):
        values = service.snapshot()['values']
        request.state.features = values
        feature = path_feature(request.url.path.rstrip('/') or '/')
        if feature and not values[feature] and cookie.verify(request.cookies.get('qq_digest_session')):
            if request.url.path.startswith('/api/') or request.method != 'GET':
                return JSONResponse(status_code=403, content={'detail': disabled_message(feature)})
            return RedirectResponse('/settings?feature=' + feature, status_code=303)
        return await call_next(request)

    @app.get('/api/features', dependencies=[Depends(require_login)])
    async def get_features():
        return service.snapshot()

    @app.put('/api/features', dependencies=[Depends(require_login)])
    async def update_features(payload: FeatureUpdate):
        try:
            return service.update(payload.values, payload.expected_revision)
        except FeatureConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return service


def disabled_message(feature):
    from ..features import FEATURES
    return FEATURES[feature][0] + '已关闭，可在设置 → 功能开关中开启'
