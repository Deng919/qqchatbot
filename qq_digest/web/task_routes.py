"""Task page and API, sharing the existing task inbox service."""
import asyncio
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..task_inbox import TaskConflictError, TaskInboxService
from .worker_threads import guarded_to_thread


class TaskFieldsPayload(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    owner: str = Field(default="", max_length=100)
    due_date: str | None = None


class TaskDecisionPayload(TaskFieldsPayload):
    title: str = Field(default="", max_length=300)
    action: Literal["confirm", "ignore"]


class MessageTaskPayload(TaskFieldsPayload):
    group_id: int
    msg_id: str = Field(min_length=1, max_length=500)


class TaskUpdatePayload(TaskFieldsPayload):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "completed", "canceled"]
    expected_revision: StrictInt = Field(ge=0)


class TaskStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "completed", "canceled"]


def add_task_routes(app, *, archive, config, templates, require_login):
    @app.get("/tasks")
    async def task_inbox_page(request: Request):
        try:
            require_login(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "tasks.html", {})

    def task_inbox_service() -> TaskInboxService:
        return TaskInboxService(archive, timezone_name=(
            config.summary.timezone if config is not None else "Asia/Shanghai"
        ))

    @app.get("/api/tasks/suggestions")
    async def api_task_suggestions(request: Request):
        require_login(request)
        return {"items": await asyncio.to_thread(task_inbox_service().suggestions)}

    @app.get("/api/tasks")
    async def api_tasks(request: Request,
                        status: Literal["open", "completed", "canceled", "all"] = "open"):
        require_login(request)
        return task_inbox_service().list_tasks(status=status)

    @app.post("/api/tasks/suggestions/{key}/decision")
    async def api_task_decision(request: Request, key: str, payload: TaskDecisionPayload):
        require_login(request)
        try:
            task = await guarded_to_thread(
                task_inbox_service().decide, key, action=payload.action,
                title=payload.title, owner=payload.owner, due_date=payload.due_date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task, "decision": payload.action}

    @app.post("/api/tasks/from-message")
    async def api_task_from_message(request: Request, payload: MessageTaskPayload):
        require_login(request)
        try:
            task = task_inbox_service().create_from_message(
                group_id=payload.group_id, msg_id=payload.msg_id,
                title=payload.title, owner=payload.owner, due_date=payload.due_date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task}

    @app.patch("/api/tasks/{task_id}")
    async def api_task_update(request: Request, task_id: int, payload: TaskUpdatePayload | TaskStatusPayload):
        require_login(request)
        try:
            if isinstance(payload, TaskStatusPayload):
                task = task_inbox_service().set_task_status(task_id, status=payload.status)
            else:
                task = task_inbox_service().update_task(
                    task_id, title=payload.title, owner=payload.owner,
                    due_date=payload.due_date, status=payload.status,
                    expected_revision=payload.expected_revision,
                )
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task}
