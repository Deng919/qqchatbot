"""Local reminders: independent SQLite work connections and authenticated APIs."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path as FilePath
from threading import Lock
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, StrictBool

from ..archive import Archive
from ..features import FeatureService
from ..operations import OperationBusy
from ..reminders import ReminderConflict, ReminderService

EntityId = Annotated[int, Path(ge=1, lt=2**63)]


class RuleValues(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(min_length=1, max_length=100, strict=True)
    kind: Literal['keyword', 'resource', 'task_due', 'failure']
    enabled: StrictBool = True
    group_ids: list[Annotated[int, Field(strict=True, ge=-(2**63), lt=2**63)]] = Field(default_factory=list, max_length=500)
    keywords: list[Annotated[str, Field(strict=True, min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    channels: list[Literal['in_app', 'windows']] = Field(default_factory=lambda: ['in_app', 'windows'], min_length=1, max_length=2)
    quiet_start: str = Field(default='22:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    quiet_end: str = Field(default='08:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')


class RuleUpdate(RuleValues):
    expected_revision: int = Field(ge=0, strict=True)


class ReadUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    read: StrictBool


def add_reminder_routes(app, *, archive, config, templates, require_login, operations):
    database_path = archive.connection.execute('PRAGMA database_list').fetchone()['file']
    timezone_name = config.summary.timezone if config else 'Asia/Shanghai'
    read_lock = Lock()

    async def execute(action, *, read=False):
        def invoke():
            # Concurrent list loads share one feature lock without rejecting
            # each other; writes still report active operation conflicts.
            with read_lock if read else nullcontext(), operations.claim('reminder_mutation'):
                worker = Archive.open(FilePath(database_path)) if database_path else archive
                try:
                    if not FeatureService(worker).enabled('reminders'):
                        raise HTTPException(403, '提醒规则已关闭，请先在功能开关中开启')
                    return action(ReminderService(worker, timezone_name=timezone_name))
                finally:
                    if worker is not archive:
                        worker.close()
        try:
            return await asyncio.to_thread(invoke) if database_path else invoke()
        except (ReminderConflict, OperationBusy) as exc:
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, '提醒处理失败，请检查存储位置后重试') from exc

    @app.get('/reminders', dependencies=[Depends(require_login)])
    async def reminders_page(request: Request):
        return templates.TemplateResponse(request, 'reminders.html', {})

    @app.get('/api/reminders/capabilities', dependencies=[Depends(require_login)])
    async def capabilities():
        sink = getattr(app.state, 'reminder_sink', None)
        return {'windows': bool(sink and getattr(sink, 'available', False)),
                'timezone': timezone_name, 'interval_seconds': 60}

    @app.get('/api/reminder-rules', dependencies=[Depends(require_login)])
    async def list_rules():
        return await execute(lambda service: service.list_rules(), read=True)

    @app.post('/api/reminder-rules/preview', dependencies=[Depends(require_login)])
    async def preview(payload: RuleValues):
        return await execute(lambda service: service.preview(payload.model_dump()), read=True)

    @app.post('/api/reminder-rules', dependencies=[Depends(require_login)])
    async def create_rule(payload: RuleValues):
        return await execute(lambda service: service.save_rule(payload.model_dump()))

    @app.patch('/api/reminder-rules/{rule_id}', dependencies=[Depends(require_login)])
    async def update_rule(rule_id: EntityId, payload: RuleUpdate):
        values = payload.model_dump(exclude={'expected_revision'})
        return await execute(lambda service: service.save_rule(values, rule_id=rule_id, expected_revision=payload.expected_revision))

    @app.delete('/api/reminder-rules/{rule_id}', dependencies=[Depends(require_login)])
    async def delete_rule(rule_id: EntityId, expected_revision: int = Query(ge=0)):
        return await execute(lambda service: service.delete_rule(rule_id, expected_revision))

    @app.get('/api/reminders', dependencies=[Depends(require_login)])
    async def list_events(page: int = Query(1, ge=1, le=2**31), page_size: int = Query(20, ge=1, le=100), unread_only: bool = False):
        return await execute(lambda service: service.list_events(page=page, page_size=page_size, unread_only=unread_only), read=True)

    @app.patch('/api/reminders/{event_id}', dependencies=[Depends(require_login)])
    async def mark_read(event_id: EntityId, payload: ReadUpdate):
        return await execute(lambda service: service.mark_read(event_id, payload.read))

    @app.post('/api/reminders/{event_id}/retry', dependencies=[Depends(require_login)])
    async def retry(event_id: EntityId):
        return await execute(lambda service: service.retry_windows(event_id))

    @app.post('/api/reminders/check', dependencies=[Depends(require_login)])
    async def check():
        def action(service):
            scan = service.scan()
            if not FeatureService(service.archive).enabled('reminders'):
                return dict(scan, delivery={'sent': 0, 'failed': 0, 'queued': scan['queued']})
            sink = getattr(app.state, 'reminder_sink', None)
            delivery = service.dispatch_windows(sink if sink and getattr(sink, 'available', False) else None)
            return dict(scan, delivery=delivery)
        return await execute(action)
