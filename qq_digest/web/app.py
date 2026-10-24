from __future__ import annotations

from .worker_threads import guarded_to_thread

import asyncio
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from ..distribution import is_public_distribution
from ..first_use import setup_in_progress
from pydantic import BaseModel, Field, field_validator, ConfigDict, StrictBool, StrictInt
from typing import Literal

from ..archive import Archive, IngestResult
from ..sender_identity import sender_fields
from ..ai.client import AIClient, AIError, AILimitExceeded
from ..ai_usage import usage_for
from ..ai.key_store import save_ui_api_key, ui_api_key_exists
from ..candidates import CandidateService
from ..failure_center import FailureCenterService
from ..report_completeness import ReportCompletenessService
from ..candidate_context import candidate_source_context
from ..collector.ntqq import NTQQCollector
from ..config import Config, ConfigError
from ..knowledge import KnowledgeWriter, remove_item
from ..scheduler import (
    daily_retry_state, run_at_for_report_date, summary_window,
)
from ..message_context import message_context
from ..report_qa import (
    NoReportEvidence, ReportNotFound, answer_report_question, load_report_evidence,
)
from ..report_sources import load_verified_report_sources
from ..report_previews import load_report_preview
from ..report_revisions import ReportRevisionService, RevisionConflict
from .. import application_runner
from .auth import PasswordHasher, SessionCookie
from .operations import OperationBusy, OperationCoordinator
from .history_inspection import add_history_inspection_routes
from .report_revisions import add_report_revision_routes
from .features import add_feature_routes
from .summary_reading_routes import add_summary_reading_routes
from .task_routes import add_task_routes
from .message_routes import add_message_routes
from .report_related_routes import add_report_related_routes
from .search_routes import add_search_routes
from .archive_qa_routes import add_archive_qa_routes
from .summary_generation_routes import add_summary_generation_routes
from .knowledge_routes import add_knowledge_routes
from .topic_routes import add_topic_routes
from .reminder_routes import add_reminder_routes
from .bookmark_routes import add_bookmark_routes
from .background_tasks import background_lifespan
from ..report_selection import selected_report_groups, selected_report_keys

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"),
                           context_processors=[lambda request: {'features': getattr(request.state, 'features', {}),
                               'storage_handoff': getattr(request.state, 'storage_handoff', {}),
                               'setup_paused': setup_in_progress(getattr(request.app.state, 'config', None))}])
templates.env.globals["public_distribution"] = is_public_distribution


class ManualRangePayload(BaseModel):
    group_ids: list[int]
    start_date: date
    end_date: date
    detail_mode: str | None = None


class AIKeyPayload(BaseModel):
    api_key: str


class CandidateEditPayload(BaseModel):
    expected_revision: int = Field(ge=0, lt=2**63, strict=True)
    candidate_type: Literal["resource", "experience"]
    title: str = Field(min_length=1, max_length=300)
    link: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=10000)
    reason: str = Field(min_length=1, max_length=2000)
    excerpt: str = Field(default="", max_length=4000)

    @field_validator("title", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()


class GroupPatchPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool | None = None
    daily_summary: StrictBool | None = None
    important_candidates: StrictBool | None = None
    read_images: StrictBool | None = None
    read_files: StrictBool | None = None
    category: Literal['tech','resource','project','study','general'] | None = None
    keywords: list[str] | None = None
    collection_window_days: StrictInt | None = Field(default=None, ge=1, le=365)
    expected_revision: StrictInt | None = Field(default=None, ge=0, lt=2**63)

    @field_validator('enabled','daily_summary','important_candidates','read_images','read_files',
                     'category','keywords','collection_window_days','expected_revision')
    @classmethod
    def provided_not_null(cls,value):
        if value is None: raise ValueError('设置不能为 null')
        return value

    @field_validator('keywords')
    @classmethod
    def valid_keywords(cls,value):
        if len(value)>20 or any(not x.strip() or len(x.strip())>50 for x in value):
            raise ValueError('keywords 必须是最多 20 个非空字符串')
        return list(dict.fromkeys(x.strip() for x in value))


class QATurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskReportPayload(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[QATurn] = Field(default_factory=list, max_length=6)
    expected_version: int | None = Field(default=None, ge=0, strict=True)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class FolderSelectionPayload(BaseModel):
    kind: Literal["storage", "export"]


class StorageMigrationPayload(BaseModel):
    destination: str = Field(min_length=1)


class ReportExportPayload(BaseModel):
    destination: str = ""
    format: Literal["markdown", "json", "both"] = "both"


class AutoStartPayload(BaseModel):
    enabled: bool


class BackupSchedulePayload(BaseModel):
    schedule: Literal["off", "daily", "weekly"]


class RestorePreviewPayload(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class RestorePayload(RestorePreviewPayload):
    destination: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(min_length=64, max_length=64)


class OpenFolderPayload(BaseModel):
    kind: Literal["storage", "export", "backup"]


def require_login(request: Request):
    cookie = request.app.state.cookie
    if not cookie.verify(request.cookies.get("qq_digest_session")):
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="未登录")
        target=request.url.path + ('?'+request.url.query if request.url.query else '')
        raise HTTPException(status_code=303, headers={"Location": '/login?'+urlencode({'next':target})})


def local_return_path(value: str) -> str:
    decoded=unquote(value)
    if (not decoded.startswith('/') or decoded.startswith('//') or '\\' in decoded
            or any(ord(c)<32 for c in decoded) or decoded.split('?')[0] in {'/login','/logout'}):
        return '/'
    return value


def _config(request: Request) -> Config:
    return request.app.state.config


def _ai_connection_view(config: Config, connection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "provider": connection.provider,
        "base_url": connection.base_url,
        "model": connection.model,
        "supports_vision": connection.supports_vision,
        "key_configured": bool(
            (connection.ui_api_key_file and ui_api_key_exists(Path(connection.ui_api_key_file)))
            or (connection.api_key_env and os.environ.get(connection.api_key_env))
            or (connection.api_key_file and Path(connection.api_key_file).is_file())
        ),
    }


def _archive(request: Request) -> Archive:
    return request.app.state.archive


def _desktop_bridge(request: Request):
    require_login(request)
    bridge = getattr(request.app.state, "desktop_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="请启动 QQ Digest 桌面程序后刷新设置页")
    return bridge


async def _desktop_mutation(request: Request, bridge, kind: str, method: str, *args):
    from ..desktop_settings import DesktopBridge
    operations = request.app.state.operations

    def invoke():
        if isinstance(bridge, DesktopBridge):
            bridge._operations = operations
            return getattr(bridge, method)(*args)
        # Alternate bridges also participate; keep the claim inside the worker
        # so disconnecting the request cannot unlock an in-flight file copy.
        with operations.claim(kind):
            return getattr(bridge, method)(*args)

    try:
        return await guarded_to_thread(invoke)
    except OperationBusy as exc:
        raise HTTPException(409, detail=str(exc)) from exc


def _utc(iso_str: str | None) -> str:
    """Format an ISO timestamp in the configured local timezone."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str


def _reference_message_count(json_path: str) -> int | None:
    """Read how many messages were actually included in the report context."""
    try:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    count = diagnostics.get("included_messages")
    return count if type(count) is int and count >= 0 else None


def _test_deepseek_connection(config: Config) -> None:
    client = AIClient(
        base_url=config.resolve_base_url(),
        api_key=config.resolve_api_key(),
        model=config.ai.model,
        json_mode=True,
        timeout_seconds=min(config.ai.timeout_seconds, 30),
        max_retries=1,
        usage_service=usage_for(config),
        purpose='connection_test',
    )
    try:
        response = client.chat(
            [
                {"role": "system", "content": "Respond with a JSON object only."},
                {"role": "user", "content": 'Connection test. Respond with JSON {"ok": true}.'},
            ]
        )
        if not isinstance(response, dict) or response.get("ok") is not True:
            from ..ai.client import reject_response
            reject_response(response)
            raise AIError("DeepSeek 连接测试未得到预期响应")
    finally:
        client.close()


def _test_ai_connection(config: Config, connection_id: str) -> None:
    from ..ai.factory import build_ai_client
    selected = next((item for item in config.ai.connections if item.id == connection_id), None)
    if selected is None:
        raise ConfigError("AI 连接不存在")
    test_config = config.model_copy(deep=True)
    test_config.ai.purpose_connections = {"connection_test": connection_id}
    test_config.ai.max_retries = 1
    test_config.ai.timeout_seconds = min(config.ai.timeout_seconds, 30)
    client = build_ai_client(test_config, purpose="connection_test")
    try:
        response = client.chat([
            {"role": "system", "content": "Respond with a JSON object only."},
            {"role": "user", "content": 'Connection test. Respond with JSON {"ok": true}.'},
        ])
        if not isinstance(response, dict) or response.get("ok") is not True:
            from ..ai.client import reject_response
            reject_response(response)
            raise AIError("AI 连接测试未得到预期响应")
    finally:
        client.close()


def create_app(
    *,
    archive: Archive,
    candidates: CandidateService,
    knowledge: KnowledgeWriter,
    password_hash: str,
    session_secret: str,
    session_hours: int = 12,
    config: Config | None = None,
    config_path: Path | None = None,
    desktop_login: bool = False,
):
    # Daily scheduler state
    scheduler_state = {
        "last_run_date": "",
        "last_attempt_date": "",
        "running": False,
        "task": None,
        "last_result": None,
    }
    if config is not None:
        latest_daily_job = archive.connection.execute(
            """
            SELECT status, started_at, error
            FROM jobs
            WHERE job_type='daily_digest'
            ORDER BY COALESCE(target_date, substr(started_at, 1, 10)) DESC,
                     job_id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_daily_job and latest_daily_job["started_at"]:
            started_at = datetime.fromisoformat(latest_daily_job["started_at"])
            if started_at.tzinfo is not None:
                started_at = started_at.astimezone(ZoneInfo(config.summary.timezone))
            attempt_date = started_at.date().isoformat()
            scheduler_state["last_attempt_date"] = attempt_date
            succeeded = latest_daily_job["status"] == "success"
            scheduler_state["last_result"] = {
                "status": latest_daily_job["status"],
                "success": succeeded,
                "error": latest_daily_job["error"] or "" if not succeeded else "",
            }
        local_now = datetime.now(ZoneInfo(config.summary.timezone))
        scheduled_target = summary_window(
            local_now, config.summary.window_mode, ZoneInfo(config.summary.timezone)
        )[0].date().isoformat()
        if daily_retry_state(
            archive.connection, local_now,
            target_hour=config.summary.hour,
            target_minute=config.summary.minute,
            max_attempts=config.summary.max_attempts,
            retry_interval_minutes=config.summary.retry_interval_minutes,
            target_date=scheduled_target,
        ).succeeded:
            scheduler_state["last_run_date"] = local_now.date().isoformat()
    operations = OperationCoordinator()
    sync_scheduler_state = {
        "running": False,
        "task": None,
        "last_run_at": "",
        "last_result": None,
    }
    if config is not None:
        latest_sync_job = archive.connection.execute(
            """
            SELECT status, finished_at, error
            FROM jobs
            WHERE job_type='message_sync'
            ORDER BY job_id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_sync_job:
            sync_scheduler_state["last_run_at"] = _utc(latest_sync_job["finished_at"])
            sync_scheduler_state["last_result"] = {
                "success": latest_sync_job["status"] == "success",
                "error": latest_sync_job["error"] or "",
            }

    def _run_daily_task(
        cfg, run_at: datetime | None = None, retry_of_job_id: int | None = None
    ):
        """Run the shared daily runtime while the Web layer owns scheduling."""
        scheduled_at = run_at or datetime.now(ZoneInfo(cfg.summary.timezone))
        return application_runner.run_daily_task(
            cfg,
            scheduled_at=scheduled_at,
            retry_of_job_id=retry_of_job_id,
            refresh=True,
        )

    def _run_manual_summary_task(cfg, payload: ManualRangePayload, *, single_day_as_daily=False, on_progress=None):
        from ..ai.factory import build_ai_client
        from ..manual_summary import ManualSummaryRequest, ManualSummaryService

        worker_archive = Archive.open(cfg.archive_path)
        ai_client = None
        try:
            ai_client = build_ai_client(cfg)
            service = ManualSummaryService(
                attachment_config=cfg,
                archive=worker_archive,
                ai_client=ai_client,
                report_dir=cfg.report_dir,
                max_context_chars=cfg.ai.max_context_chars,
                timezone_name=cfg.summary.timezone,
                knowledge_paths=cfg.resolve_knowledge_paths(),
                single_day_as_daily=single_day_as_daily,
            )
            result = service.run(
                ManualSummaryRequest(
                    group_ids=payload.group_ids,
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                ),
                on_progress=on_progress,
            )
            return {
                "status": result.status,
                "created_reports": [
                    asdict(item) for item in result.created_reports
                ],
                "reused_reports": [
                    asdict(item) for item in result.reused_reports
                ],
                "skipped_groups": result.skipped_groups,
                "failed_groups": [
                    asdict(item) for item in result.failed_groups
                ],
            }
        finally:
            if ai_client is not None:
                ai_client.close()
            worker_archive.close()

    lifespan = background_lifespan(
        config=config, archive=archive, candidates=candidates, knowledge=knowledge,
        operations=operations, scheduler_state=scheduler_state,
        sync_scheduler_state=sync_scheduler_state, run_daily_task=_run_daily_task,
    )

    app = FastAPI(title="QQ Digest", lifespan=lifespan)
    cookie = SessionCookie(session_secret, session_hours * 3600)
    app.state.archive = archive
    app.state.candidates = candidates
    app.state.knowledge = knowledge
    app.state.password_hash = password_hash
    app.state.cookie = cookie
    if desktop_login:
        from .desktop_session import add_desktop_session_routes
        add_desktop_session_routes(app, session_secret)
    app.state.config = config
    app.state.operations = operations
    operations.activity_root = lambda: app.state.config.data_dir if app.state.config is not None else None
    app.state.desktop_bridge = None
    from .desktop_update_routes import add_desktop_update_routes
    add_desktop_update_routes(app, templates=templates, require_login=require_login,
                             desktop_bridge=_desktop_bridge, desktop_mutation=_desktop_mutation)
    from .first_use_routes import add_first_use_routes
    add_first_use_routes(app, config=config, config_path=config_path, archive=archive,
                         templates=templates, require_login=require_login, operations=operations)
    from .ai_connection_routes import add_ai_connection_routes
    add_ai_connection_routes(app, require_login=require_login,
        test_connection=lambda cfg, ident: _test_ai_connection(cfg, ident))
    feature_service = add_feature_routes(app, archive=archive, cookie=cookie, require_login=require_login, operations=operations)
    from .secondhand_routes import add_secondhand_routes
    add_secondhand_routes(app,archive=archive,config=config,templates=templates,require_login=require_login,operations=operations)
    add_summary_reading_routes(app, archive=archive, config=config, require_login=require_login)
    add_task_routes(app, archive=archive, config=config, templates=templates, require_login=require_login)
    add_message_routes(app, archive=archive, config=config, require_login=require_login)
    add_report_related_routes(app, archive=_archive, config=_config, require_login=require_login)
    from .failure_ignore_routes import add_failure_ignore_routes
    add_failure_ignore_routes(app, archive=_archive, require_login=require_login)
    from .attachment_routes import add_attachment_routes
    from .group_batch_routes import add_group_batch_routes
    add_group_batch_routes(app,archive=_archive,config=_config,require_login=require_login,operations=operations)
    add_attachment_routes(app,archive=_archive,config=_config,require_login=require_login,operations=operations)
    from .ai_usage_routes import add_ai_usage_routes
    add_ai_usage_routes(app,config=_config,require_login=require_login,operations=operations)
    from .storage_routes import add_storage_routes
    add_storage_routes(app, require_login=require_login, desktop_bridge=_desktop_bridge,
                       desktop_mutation=_desktop_mutation)
    add_search_routes(app, archive=archive, config=config, require_login=require_login)
    add_archive_qa_routes(app, archive=archive, config=config, templates=templates,
        require_login=require_login, operations=operations)
    add_topic_routes(app, archive=archive, config=config, templates=templates,
                     require_login=require_login, operations=operations)
    add_reminder_routes(app, archive=archive, config=config, templates=templates,
                        require_login=require_login, operations=operations)
    add_bookmark_routes(app, archive=archive, config=config, templates=templates,
                        require_login=require_login, operations=operations)
    knowledge_library = add_knowledge_routes(app, archive=archive, knowledge=knowledge,
        config=config, templates=templates, require_login=require_login, operations=operations)
    add_summary_generation_routes(app, archive=archive, config=config, require_login=require_login,
                                  run_summary=_run_manual_summary_task, operations=operations)
    add_history_inspection_routes(app, archive=_archive, config=_config,
                                  operations=operations, require_login=require_login)
    add_report_revision_routes(app, archive=archive, config=config,
                               operations=operations, require_login=require_login)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/desktop-info")
    async def desktop_info():
        return {
            "config_signature": getattr(app.state, "desktop_config_signature", ""),
            "settings_api_version": getattr(app.state, "desktop_settings_api_version", 0),
            "backend_id": getattr(app.state, "desktop_backend_id", ""),
        }

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    @app.get("/login")
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {'next_path':local_return_path(request.query_params.get('next','/'))})

    @app.post("/login")
    async def login(request: Request, password: str = Form(...), next_path: str = Form('/',alias='next')):
        target=local_return_path(next_path)
        if not PasswordHasher.verify(password, app.state.password_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "密码错误",'next_path':target}, status_code=401
            )
        response = RedirectResponse(target, status_code=303)
        response.set_cookie("qq_digest_session", cookie.issue(), httponly=True)
        return response

    @app.post("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("qq_digest_session")
        return response

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    @app.get("/")
    async def dashboard(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "reports.html", {})

    @app.get("/overview")
    async def overview_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "dashboard.html", {})

    @app.get("/groups")
    async def groups_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "groups.html", {})

    @app.get("/collect")
    async def collect_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "collect.html", {})

    @app.get("/reports")
    async def reports_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "reports.html", {})

    @app.get("/catchup")
    async def catchup_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/reports?view=points", status_code=303)

    @app.get("/failures")
    async def failure_center_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "failures.html", {})

    @app.get("/api/failures")
    async def api_failures(request: Request):
        require_login(request)
        return FailureCenterService(_archive(request)).list_items()

    @app.post("/api/failures/jobs/{job_id}/retry")
    async def api_retry_failed_job(request: Request, job_id: int):
        require_login(request)
        cfg = _config(request)
        if cfg is None:
            raise HTTPException(status_code=503, detail="桌面配置不可用")
        row = _archive(request).connection.execute(
            "SELECT job_type,status,target_date FROM jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="失败任务不存在")
        if row["status"] not in {"failed", "partial_success"}:
            raise HTTPException(status_code=409, detail="该任务当前不需要重试")
        kind = row["job_type"]
        if kind not in {"daily_digest", "message_sync"}:
            raise HTTPException(status_code=400, detail="此任务暂无直接重试方式")
        if kind == "message_sync" and not cfg.ntqq.enabled:
            raise HTTPException(status_code=503, detail="NTQQ 采集未启用")
        run_at = None
        if kind == "daily_digest":
            try:
                target = date.fromisoformat(row["target_date"] or "")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="旧任务缺少有效报告日期") from exc
            now = datetime.now(ZoneInfo(cfg.summary.timezone))
            if target > now.date():
                raise HTTPException(status_code=400, detail="不能重试未来日期")
            if target == now.date() and cfg.summary.window_mode == "today":
                run_at = now
            else:
                run_at = run_at_for_report_date(
                    target, cfg.summary.window_mode, ZoneInfo(cfg.summary.timezone)
                )
                if run_at > now:
                    raise HTTPException(status_code=400, detail="报告日期的完整窗口尚未结束")
        try:
            with operations.claim("daily" if kind == "daily_digest" else "sync"):
                current = _archive(request).connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if (current is None or current["status"] not in {"failed", "partial_success"}
                        or FailureCenterService(_archive(request)).job_is_recovered(current)):
                    raise HTTPException(status_code=409, detail="该失败已恢复或任务状态已变化")
                if kind == "daily_digest":
                    scheduler_state["running"] = True
                    try:
                        result = await guarded_to_thread(
                            _run_daily_task, cfg, run_at, job_id
                        )
                    finally:
                        scheduler_state["running"] = False
                    status = result.get("status", "failed")
                else:
                    from ..sync import run_message_sync
                    sync_scheduler_state["running"] = True
                    try:
                        result = await guarded_to_thread(
                            run_message_sync, config=cfg, retry_of_job_id=job_id
                        )
                    finally:
                        sync_scheduler_state["running"] = False
                    status = "success" if result.success else "failed"
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc
        retry = _archive(request).connection.execute(
            "SELECT job_id,status FROM jobs WHERE retry_of_job_id=? ORDER BY job_id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return {
            "status": retry["status"] if retry else status,
            "retry_job_id": retry["job_id"] if retry else None,
        }

    @app.post("/api/failures/notifications/{send_id}/retry")
    async def api_retry_failed_notification(request: Request, send_id: int):
        require_login(request)
        row = _archive(request).connection.execute(
            "SELECT status FROM send_log WHERE send_id=?", (send_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="通知记录不存在")
        if row["status"] != "failed":
            raise HTTPException(status_code=409, detail="通知已在队列中或已发送")
        cfg = _config(request)
        if cfg is None or not cfg.qq_bot.enabled or not cfg.qq_bot.app_id:
            raise HTTPException(status_code=503, detail="请先启用 QQ Bot 通知")
        if not _archive(request).retry_failed_notification(send_id):
            raise HTTPException(status_code=409, detail="通知状态已改变，请刷新页面")
        return {"status": "pending_send", "send_id": send_id}

    @app.get("/search")
    async def search_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "search.html", {})

    @app.get("/candidates")
    async def candidate_list(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {"candidates": candidates.pending()},
        )

    @app.get("/ai-settings")
    async def ai_settings_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/settings?section=ai", status_code=303)

    @app.get("/settings")
    async def settings_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "settings.html", {})

    @app.get("/api/desktop-settings")
    async def desktop_settings_state(request: Request):
        bridge = _desktop_bridge(request)
        return await guarded_to_thread(bridge.get_settings)

    @app.post("/api/desktop-settings/choose-folder")
    async def desktop_choose_folder(request: Request, payload: FolderSelectionPayload):
        bridge = _desktop_bridge(request)
        try:
            return {"path": await guarded_to_thread(bridge.choose_folder, payload.kind)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"文件夹窗口打开失败：{exc}") from exc

    @app.post("/api/desktop-settings/migrate")
    async def desktop_migrate(request: Request, payload: StorageMigrationPayload):
        bridge = _desktop_bridge(request)
        try:
            return await _desktop_mutation(request, bridge, 'storage_mutation',
                                           'migrate_storage', payload.destination)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/export")
    async def desktop_export(request: Request, payload: ReportExportPayload):
        bridge = _desktop_bridge(request)
        return await guarded_to_thread(bridge.export_reports, payload.destination, payload.format)

    @app.post("/api/desktop-settings/auto-start")
    async def desktop_auto_start(request: Request, payload: AutoStartPayload):
        bridge = _desktop_bridge(request)
        return await guarded_to_thread(bridge.set_auto_start, payload.enabled)

    @app.post("/api/desktop-settings/backup")
    async def desktop_backup(request: Request):
        bridge = _desktop_bridge(request)
        try:
            return await _desktop_mutation(request, bridge, 'backup', 'backup_data')
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/backup-schedule")
    async def desktop_backup_schedule(request: Request, payload: BackupSchedulePayload):
        bridge = _desktop_bridge(request)
        return await guarded_to_thread(bridge.set_backup_schedule, payload.schedule)

    @app.post("/api/desktop-settings/choose-backup")
    async def desktop_choose_backup(request: Request):
        bridge = _desktop_bridge(request)
        try:
            return {"path": await guarded_to_thread(bridge.choose_backup_file)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"备份文件窗口打开失败：{exc}") from exc

    @app.post("/api/desktop-settings/restore-preview")
    async def desktop_restore_preview(request: Request, payload: RestorePreviewPayload):
        bridge = _desktop_bridge(request)
        try:
            return await guarded_to_thread(bridge.preview_restore, payload.path)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/restore")
    async def desktop_restore(request: Request, payload: RestorePayload):
        bridge = _desktop_bridge(request)
        try:
            return await _desktop_mutation(request, bridge, 'storage_mutation',
                'restore_backup', payload.path, payload.destination, payload.sha256)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/open-folder")
    async def desktop_open_folder(request: Request, payload: OpenFolderPayload):
        bridge = _desktop_bridge(request)
        return await guarded_to_thread(bridge.open_folder, payload.kind)

    # ------------------------------------------------------------------
    # API: DeepSeek settings
    # ------------------------------------------------------------------
    @app.get("/api/ai-settings")
    async def api_ai_settings(request: Request):
        require_login(request)
        cfg = _config(request)
        result = {
            "base_url": cfg.ai.base_url,
            "model": cfg.ai.model,
            "provider_priority": cfg.ai.provider_priority,
            **({} if is_public_distribution() else {"bridge_model": cfg.ai.bridge_model}),
            "key_configured": bool(
                cfg.ai.ui_api_key_file
                and ui_api_key_exists(Path(cfg.ai.ui_api_key_file))
            ),
        }
        if cfg.ai.connections or cfg.ai.purpose_connections:
            result["connections"] = [_ai_connection_view(cfg, item) for item in cfg.ai.connections]
            result["purpose_connections"] = {
                purpose: cfg.ai.purpose_connections.get(purpose, "")
                for purpose in ("summary", "report_qa", "archive_qa", "vision")
            }
        return result

    @app.put("/api/ai-settings/key")
    async def api_save_ai_key(request: Request, payload: AIKeyPayload):
        require_login(request)
        cfg = _config(request)
        if not cfg.ai.ui_api_key_file:
            raise HTTPException(status_code=503, detail="本地 API Key 保存路径未配置")
        try:
            await guarded_to_thread(
                save_ui_api_key, Path(cfg.ai.ui_api_key_file), payload.api_key
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="保存 API Key 失败") from exc
        return {"ok": True, "key_configured": True}

    @app.post("/api/ai-settings/test")
    async def api_test_ai_settings(request: Request):
        require_login(request)
        cfg = _config(request)
        try:
            await guarded_to_thread(_test_deepseek_connection, cfg)
        except AILimitExceeded as exc:
            raise HTTPException(429, detail=str(exc)) from exc
        except (AIError, ConfigError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="DeepSeek 连接失败，请检查 API Key、接口配置和网络",
            ) from exc
        return {"ok": True}

    # ------------------------------------------------------------------
    # API: Stats
    # ------------------------------------------------------------------
    @app.get("/api/stats")
    async def api_stats(request: Request):
        require_login(request)
        ar = _archive(request)
        enabled = ar.enabled_groups()
        total_msgs = ar.connection.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        pending = ar.connection.execute("SELECT COUNT(*) AS c FROM candidates WHERE status='pending'").fetchone()["c"]
        daily_reports = ar.connection.execute(
            "SELECT COUNT(*) AS c FROM reports"
        ).fetchone()["c"]
        range_reports = ar.connection.execute(
            "SELECT COUNT(*) AS c FROM manual_reports"
        ).fetchone()["c"]
        return {
            "enabled_groups": len(enabled),
            "total_messages": total_msgs,
            "pending_candidates": pending,
            "total_reports": daily_reports + range_reports,
        }

    @app.get("/api/health")
    async def api_health(request: Request):
        require_login(request)
        ar = _archive(request)
        ar.connection.execute("SELECT 1").fetchone()
        last_sync = ar.connection.execute(
            "SELECT MAX(last_success_at) FROM sync_state"
        ).fetchone()[0]
        latest_job = ar.connection.execute(
            "SELECT status, finished_at FROM jobs ORDER BY job_id DESC LIMIT 1"
        ).fetchone()
        latest_daily = ar.connection.execute(
            """SELECT status, target_date, started_at FROM jobs
               WHERE job_type='daily_digest'
               ORDER BY COALESCE(target_date, substr(started_at, 1, 10)) DESC,
                        job_id DESC LIMIT 1"""
        ).fetchone()
        latest_daily_date = latest_daily["target_date"] if latest_daily else ""
        if latest_daily and not latest_daily_date and latest_daily["started_at"]:
            cfg = _config(request)
            local_timezone = ZoneInfo(cfg.summary.timezone) if cfg else ZoneInfo("Asia/Shanghai")
            started = datetime.fromisoformat(latest_daily["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            latest_daily_date = summary_window(
                started, cfg.summary.window_mode if cfg else "today", local_timezone
            )[0].date().isoformat()
        failed_notifications = ar.connection.execute(
            "SELECT COUNT(*) FROM send_log WHERE status='failed'"
        ).fetchone()[0]
        return {
            "service": "running", "archive": "ready",
            "last_sync_at": _utc(last_sync) if last_sync else "",
            "latest_job_status": latest_job["status"] if latest_job else "",
            "latest_job_at": _utc(latest_job["finished_at"]) if latest_job else "",
            "latest_daily_status": latest_daily["status"] if latest_daily else "",
            "latest_daily_date": latest_daily_date or "",
            "failed_notifications": failed_notifications,
            "daily_coverage": ReportCompletenessService(ar).daily(latest_daily_date or ""),
        }

    @app.get("/api/search/messages/{group_id}/{msg_id}/context")
    async def api_search_message_context(request: Request, group_id: int, msg_id: str,
                                         direction: str = "around", cursor: str | None = None):
        require_login(request)
        try:
            return message_context(_archive(request), group_id, msg_id,
                                   direction=direction, cursor=cursor)
        except LookupError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # API: Jobs
    # ------------------------------------------------------------------
    @app.get("/api/jobs")
    async def api_jobs(request: Request):
        require_login(request)
        ar = _archive(request)
        rows = ar.connection.execute(
            "SELECT * FROM jobs ORDER BY job_id DESC LIMIT 10"
        ).fetchall()
        return {"jobs": [{"job_type": r["job_type"], "status": r["status"],
                           "target_date": r["target_date"],
                           "started_at": _utc(r["started_at"]) if r["started_at"] else "",
                           "finished_at": _utc(r["finished_at"]) if r["finished_at"] else "",
                           "error": (r["error"] or "")[:500]}
                          for r in rows]}

    # ------------------------------------------------------------------
    # API: Groups
    # ------------------------------------------------------------------
    @app.get("/api/groups")
    async def api_groups(request: Request):
        require_login(request)
        ar = _archive(request)
        result = []
        for g in ar.all_groups():
            stats = ar.connection.execute(
                """
                SELECT COUNT(*) AS message_count, MAX(timestamp) AS latest_message_at
                FROM messages WHERE group_id=?
                """,
                (g.group_id,),
            ).fetchone()
            sync = ar.connection.execute(
                "SELECT last_timestamp, status, updated_at, error, last_success_at FROM sync_state WHERE group_id=?",
                (g.group_id,),
            ).fetchone()
            latest = stats["latest_message_at"]
            result.append({
                "group_id": g.group_id,
                "name": g.name,
                "enabled": g.enabled,
                "daily_summary": g.daily_summary,
                "category": g.category,
                "keywords": g.keywords,
                "collection_window_days": g.collection_window_days,
                "important_candidates": g.important_candidates,
                "read_images": bool(g.read_images),
                "read_files": bool(g.read_files),
                "revision": g.revision,
                "message_count": stats["message_count"],
                "latest_message_at": _utc(latest) if latest else None,
                "last_sync": _utc(sync["last_timestamp"]) if sync and sync["last_timestamp"] else "未同步",
                "sync_status": sync["status"] if sync else "never_synced",
                "sync_error": sync["error"] if sync else "",
                "last_sync_attempt": _utc(sync["updated_at"]) if sync else None,
                "last_success_at": _utc(sync["last_success_at"]) if sync and sync["last_success_at"] else None,
            })
        return {"groups": result}

    @app.get("/api/groups/stats")
    async def api_groups_stats(request: Request):
        require_login(request)
        ar = _archive(request)
        groups = ar.enabled_groups()
        result = []
        for g in groups:
            cnt = ar.connection.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE group_id=?", (g.group_id,)
            ).fetchone()["c"]
            sync = ar.connection.execute(
                "SELECT last_success_at FROM sync_state WHERE group_id=?", (g.group_id,)
            ).fetchone()
            result.append({"name": g.name, "message_count": cnt,
                           "last_sync": _utc(sync["last_success_at"]) if sync and sync["last_success_at"] else "未同步"})
        return {"groups": result}

    @app.get("/api/discover-groups")
    async def api_discover_groups(request: Request):
        require_login(request)
        cfg = _config(request)
        if not cfg or not cfg.ntqq.enabled or not cfg.ntqq.db_dir:
            raise HTTPException(status_code=400, detail="NTQQ 未启用")
        collector = NTQQCollector(
            db_dir=cfg.ntqq.db_dir,
            qq_number=cfg.ntqq.qq_number,
            timezone_name=cfg.ntqq.timezone,
        )
        try:
            groups = await guarded_to_thread(collector.discover_groups)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"群聊扫描失败: {exc}") from exc
        result = []
        ar = _archive(request)
        for group in groups:
            group_id = group["group_id"]
            archived = ar.connection.execute(
                "SELECT MAX(timestamp) AS latest FROM messages WHERE group_id=?",
                (group_id,),
            ).fetchone()["latest"]
            sync = ar.connection.execute(
                "SELECT last_timestamp FROM sync_state WHERE group_id=?",
                (group_id,),
            ).fetchone()
            source_latest = group["latest_message_at"]
            archived_latest = datetime.fromisoformat(archived) if archived else None
            source_has_newer = bool(
                source_latest and (archived_latest is None or source_latest > archived_latest)
            )
            last_sync = datetime.fromisoformat(sync["last_timestamp"]) if sync and sync["last_timestamp"] else None
            result.append({
                "group_id": group_id,
                "name": group["name"],
                "message_count_30d": group["message_count_30d"],
                "latest_message_at": source_latest.isoformat() if source_latest else None,
                "archive_latest_message_at": archived,
                "source_has_newer_messages": source_has_newer,
                "suspected_gap": bool(source_has_newer and last_sync and last_sync >= source_latest),
            })
        return {"groups": result}

    @app.post("/api/groups")
    async def api_add_group(request: Request):
        require_login(request)
        body = await request.json()
        gid = body.get("group_id")
        name = body.get("name", "")
        if not gid:
            raise HTTPException(status_code=400, detail="group_id 必填")
        ar = _archive(request)
        from ..models import GroupConfig
        gc = GroupConfig(group_id=gid, name=name, enabled=True,
                         daily_summary=True,
                         keywords=[], important_candidates=True,
                         collection_window_days=30,
                         category=body.get("category", "general"))
        ar.upsert_groups([gc])
        ar.connection.commit()
        return {"ok": True}

    @app.patch("/api/groups/{group_id}")
    async def api_update_group(request: Request, group_id: int, payload: GroupPatchPayload):
        require_login(request)
        body = payload.model_dump(exclude_unset=True, exclude={'expected_revision'})
        if len(body)>1 and payload.expected_revision is None:
            raise HTTPException(422,'保存多项群设置时必须提供原始版本，请先读取群设置')
        ar = _archive(request)
        allowed = {
            "enabled",
            "daily_summary",
            "category",
            "important_candidates",
            "keywords",
            "collection_window_days",
            "read_images","read_files",
        }
        for key in ('read_images','read_files'):
            if key in body and type(body[key]) is not bool:raise HTTPException(400,key+' 必须是开关值')
        sets = []
        vals = []
        for k, v in body.items():
            if k in allowed:
                sets.append(f"{k}=?")
                if k in ("enabled", "daily_summary", "important_candidates","read_images","read_files"):
                    vals.append(1 if v else 0)
                elif k == "keywords":
                    if (
                        not isinstance(v, list)
                        or len(v) > 20
                        or any(
                            not isinstance(item, str)
                            or not item.strip()
                            or len(item.strip()) > 50
                            for item in v
                        )
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail="keywords 必须是最多 20 个非空字符串",
                        )
                    vals.append(
                        json.dumps(
                            list(dict.fromkeys(item.strip() for item in v)),
                            ensure_ascii=False,
                        )
                    )
                elif k == "collection_window_days":
                    if type(v) is not int or not 1 <= v <= 365:
                        raise HTTPException(
                            status_code=400,
                            detail="collection_window_days 必须是 1 到 365 的整数",
                        )
                    vals.append(v)
                else:
                    vals.append(v)
        if not sets:
            raise HTTPException(status_code=400, detail="无有效字段")
        sets.append('revision=revision+1')
        vals.append(group_id)
        where='group_id=?'
        if payload.expected_revision is not None:
            where+=' AND revision=?'
            vals.append(payload.expected_revision)
        from contextlib import nullcontext
        try:
            with operations.claim('group_mutation') if any(key in body for key in ('read_images','read_files')) else nullcontext():
                with ar.transaction():
                    updated=ar.connection.execute(f"UPDATE groups SET {', '.join(sets)} WHERE {where}", vals)
                    if not updated.rowcount:
                        if not ar.connection.execute('SELECT 1 FROM groups WHERE group_id=?',(group_id,)).fetchone():
                            raise HTTPException(404,'群组不存在')
                        raise HTTPException(409,'群设置已在其他窗口更新，输入已保留，请刷新后核对再保存')
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc
        return {"ok": True, 'revision':ar.connection.execute('SELECT revision FROM groups WHERE group_id=?',(group_id,)).fetchone()[0]}

    @app.delete("/api/groups/{group_id}")
    async def api_delete_group(request: Request, group_id: int):
        require_login(request)
        ar = _archive(request)
        try:
            with operations.claim("group_mutation"):
                try:
                    result = ar.delete_group(group_id)
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from exc

                cfg = _config(request)
                if cfg:
                    report_root = cfg.report_dir.resolve()
                    for raw_path in result.report_paths:
                        path = Path(raw_path).resolve()
                        if path.is_relative_to(report_root):
                            path.unlink(missing_ok=True)
                return {"ok": True, "deleted": result.deleted}
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc

    # ------------------------------------------------------------------
    # API: Collect
    # ------------------------------------------------------------------
    @app.post("/api/collect")
    async def api_collect(request: Request):
        require_login(request)
        body = await request.json()
        gid = body.get("group_id")
        start_str = body.get("start")
        end_str = body.get("end")
        refresh_requested = body.get("refresh", False)
        dry_run = body.get("dry_run", False)
        if not gid or not start_str or not end_str:
            raise HTTPException(status_code=400, detail="缺少参数")
        if not isinstance(refresh_requested, bool):
            raise HTTPException(status_code=400, detail="refresh 必须是布尔值")
        if not isinstance(dry_run, bool):
            raise HTTPException(status_code=400, detail="dry_run 必须是布尔值")
        cfg = _config(request)
        tz = ZoneInfo(cfg.summary.timezone if cfg else "Asia/Shanghai")
        try:
            start = datetime.fromisoformat(start_str + "T00:00:00").replace(tzinfo=tz)
            end = datetime.fromisoformat(end_str + "T23:59:59").replace(tzinfo=tz)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="日期格式无效") from exc
        if start > end:
            raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
        if dry_run and (end.date() - start.date()).days > 90:
            raise HTTPException(status_code=400, detail="单次缺口检查最多支持 91 天")
        if not cfg or not cfg.ntqq.enabled:
            raise HTTPException(status_code=400, detail="NTQQ 未启用")
        if not isinstance(gid, int) or isinstance(gid, bool) or not any(
            group.group_id == gid for group in _archive(request).all_groups()
        ):
            raise HTTPException(status_code=404, detail="群组不存在")

        def collect_in_worker():
            if refresh_requested:
                from ..refresh import refresh_database

                refreshed = refresh_database(
                    qq_number=cfg.ntqq.qq_number,
                    output_dir=cfg.ntqq.db_dir,
                    snapshot_root=cfg.work_dir / "snapshots",
                    cache_owner=cfg.data_dir,
                )
                if not refreshed.success:
                    raise RuntimeError(f"数据库刷新失败：{refreshed.message}")
            collector = NTQQCollector(
                db_dir=cfg.ntqq.db_dir,
                qq_number=cfg.ntqq.qq_number,
                timezone_name=cfg.ntqq.timezone,
            )
            msgs = list(collector.collect(gid, start, end))
            worker_archive = Archive.open(cfg.archive_path)
            try:
                stored_ids = {
                    row["msg_id"] for row in worker_archive.connection.execute(
                        "SELECT msg_id FROM messages WHERE group_id=?",
                        (gid,),
                    )
                }
                missing_dates: dict[str, int] = {}
                seen_ids: set[str] = set()
                for message in msgs:
                    if message.msg_id in stored_ids or message.msg_id in seen_ids:
                        continue
                    seen_ids.add(message.msg_id)
                    day = message.timestamp.astimezone(tz).date().isoformat()
                    missing_dates[day] = missing_dates.get(day, 0) + 1
                if dry_run:
                    ingest = IngestResult(inserted=0, skipped=len(msgs) - len(seen_ids))
                else:
                    ingest = worker_archive.ingest(msgs)
                    from ..attachments import AttachmentService
                    AttachmentService(worker_archive,cfg).prepare(msgs)
                    worker_archive.mark_manual_collect_success(group_id=gid)
            finally:
                worker_archive.connection.close()
            missing_days = [
                {"date": day, "count": missing_dates[day]}
                for day in sorted(missing_dates)
            ]
            return msgs, ingest, missing_days

        try:
            with operations.claim("collect"):
                msgs, ingest, missing_days = await guarded_to_thread(collect_in_worker)
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc
        except Exception as exc:
            if not dry_run:
                worker_archive = Archive.open(cfg.archive_path)
                try:
                    worker_archive.mark_sync_failure(
                        group_id=gid,
                        status="manual_collect_failed",
                        error=str(exc),
                    )
                finally:
                    worker_archive.close()
            raise HTTPException(status_code=503, detail=f"采集失败：{exc}") from exc

        preview = msgs[-500:]
        return {
            "messages": [
                {
                    "msg_id": m.msg_id,
                    "sender_qq": m.sender_qq,
                    **sender_fields({'sender_qq':m.sender_qq,'content_json':m.content_json}),
                    "timestamp": m.timestamp.isoformat(),
                    "message_type": m.message_type,
                    "text": m.text,
                }
                for m in ([] if dry_run else preview)
            ],
            "total": len(msgs),
            "inserted": ingest.inserted,
            "skipped": ingest.skipped,
            "missing_days": missing_days,
            "refreshed": refresh_requested,
            "dry_run": dry_run,
            "preview_truncated": len(preview) < len(msgs),
        }

    # ------------------------------------------------------------------
    # API: Scheduler status
    # ------------------------------------------------------------------
    @app.get("/api/scheduler")
    async def api_scheduler(request: Request):
        require_login(request)
        cfg = _config(request)
        tz = ZoneInfo(cfg.summary.timezone) if cfg else ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
        return {
            "enabled": bool(cfg and not setup_in_progress(cfg) and feature_service.enabled('auto_daily')),
            "setup_paused": setup_in_progress(cfg),
            "schedule": f"{cfg.summary.hour:02d}:{cfg.summary.minute:02d}" if cfg else "",
            "timezone": cfg.summary.timezone if cfg else "Asia/Shanghai",
            "running": scheduler_state["running"],
            "last_run_date": scheduler_state["last_run_date"],
            "last_result": scheduler_state["last_result"],
            "next_check": "within 60s",
            "collection": {
                "enabled": bool(cfg and not setup_in_progress(cfg) and cfg.collection.enabled and cfg.ntqq.enabled and feature_service.enabled('auto_collection')),
                "interval_minutes": cfg.collection.interval_minutes if cfg else 0,
                "running": sync_scheduler_state["running"],
                "last_run_at": sync_scheduler_state["last_run_at"],
                "last_result": sync_scheduler_state["last_result"],
            },
        }

    # ------------------------------------------------------------------
    # API: Refresh NTQQ Database
    # ------------------------------------------------------------------
    @app.post("/api/refresh")
    async def api_refresh(request: Request):
        require_login(request)
        cfg = _config(request)
        if not cfg or not cfg.ntqq.enabled:
            raise HTTPException(status_code=400, detail="NTQQ 未启用")
        from ..refresh import refresh_database

        try:
            with operations.claim("refresh"):
                result = await guarded_to_thread(
                    lambda: refresh_database(
                        qq_number=cfg.ntqq.qq_number,
                        output_dir=cfg.ntqq.db_dir,
                        snapshot_root=cfg.work_dir / "snapshots",
                        cache_owner=cfg.data_dir,
                    )
                )
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc
        return {
            "success": result.success,
            "message": result.message,
            "files_decrypted": result.files_decrypted,
            "files_failed": result.files_failed,
            "output_dir": result.output_dir,
            "duration_seconds": round(result.duration_seconds, 1),
            "errors": result.errors,
        }

    # ------------------------------------------------------------------
    # API: Reports
    # ------------------------------------------------------------------
    @app.get("/api/reports")
    async def api_reports(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        group_id: int | None = None,
        group_ids: list[int] | None = Query(None),
        kind: Literal["all", "daily", "range"] = "all",
        date_from: date | None = None,
        date_to: date | None = None,
        report_keys: list[str] | None = Query(None),
    ):
        require_login(request)
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
        ar = _archive(request)
        source = """WITH all_reports AS (
            SELECT 'daily' AS report_kind, r.report_id AS report_id, r.group_id,
                   g.name AS group_name, r.report_date AS window_start_date,
                   r.report_date AS window_end_date, r.json_path, r.candidate_ids,
                   r.created_at
            FROM reports r JOIN groups g ON g.group_id=r.group_id
            UNION ALL
            SELECT 'range', r.manual_report_id, r.group_id, g.name,
                   r.start_date, r.end_date, r.json_path, r.candidate_ids, r.updated_at
            FROM manual_reports r JOIN groups g ON g.group_id=r.group_id
        )"""
        clauses = []
        params: list[object] = []
        if kind != "all":
            clauses.append("report_kind=?")
            params.append(kind)
        try:
            selection=selected_report_groups(group_id,group_ids)
            keys=selected_report_keys(report_keys)
        except ValueError as exc:
            raise HTTPException(422,detail=str(exc)) from exc
        if selection:
            clauses.append('group_id IN ('+','.join('?' for _ in selection)+')')
            params.extend(selection)
        if keys:
            clauses.append("report_kind || ':' || report_id IN ("+','.join('?' for _ in keys)+')')
            params.extend(keys)
        if date_from is not None:
            clauses.append("window_end_date>=?")
            params.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("window_start_date<=?")
            params.append(date_to.isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = ar.connection.execute(
            source + " SELECT COUNT(*) FROM all_reports" + where, params
        ).fetchone()[0]
        rows = ar.connection.execute(
            source + " SELECT * FROM all_reports" + where
            + " ORDER BY window_end_date DESC, window_start_date DESC, created_at DESC"
            + " LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        completeness = ReportCompletenessService(ar)
        reports = [
            {
                "report_kind": row["report_kind"],
                "report_id": int(row["report_id"]),
                "report_key": f"{row['report_kind']}:{row['report_id']}",
                "report_date": row["window_end_date"],
                "window_start_date": row["window_start_date"],
                "window_end_date": row["window_end_date"],
                "group_name": row["group_name"],
                "group_id": row["group_id"],
                "candidate_count": len(json.loads(row["candidate_ids"])),
                "created_at": _utc(row["created_at"]),
                "reference_message_count": _reference_message_count(row["json_path"]),
                "preview": load_report_preview(row["json_path"]),
                "completeness": completeness.report(
                    row["json_path"], row["report_kind"], row["window_end_date"]
                ),
            }
            for row in rows
        ]
        return {"reports": reports, "total": total, "page": page, "page_size": page_size}

    @app.post("/api/reports/range")
    async def api_create_range_report(
        request: Request, payload: ManualRangePayload
    ):
        require_login(request)
        cfg = _config(request)
        if not cfg:
            raise HTTPException(status_code=500, detail="配置不可用")
        try:
            with operations.claim("manual_summary"):
                try:
                    return await guarded_to_thread(
                        _run_manual_summary_task, cfg, payload
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc

    @app.get("/api/reports/{report_id}")
    async def api_report_detail(request: Request, report_id: int):
        require_login(request)
        ar = _archive(request)
        row = ar.connection.execute(
            "SELECT * FROM reports WHERE report_id=?", (report_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="报告不存在")
        md_path = Path(row["markdown_path"])
        markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else "报告文件不存在"
        return {"markdown": markdown, "report_date": row["report_date"],
                "completeness": ReportCompletenessService(ar).report(
                    row["json_path"], "daily", row["report_date"])}

    @app.get("/api/reports/{report_kind}/{report_id}")
    async def api_typed_report_detail(
        request: Request, report_kind: str, report_id: int
    ):
        require_login(request)
        ar = _archive(request)
        if report_kind == "daily":
            row = ar.connection.execute(
                "SELECT * FROM reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="报告不存在")
            start_date = end_date = row["report_date"]
        elif report_kind == "range":
            row = ar.manual_report_by_id(report_id)
            if row is None:
                raise HTTPException(status_code=404, detail="报告不存在")
            start_date = row["start_date"]
            end_date = row["end_date"]
        else:
            raise HTTPException(status_code=404, detail="报告类型不存在")
        markdown_path = Path(row["markdown_path"])
        group = ar.connection.execute(
            "SELECT name FROM groups WHERE group_id=?", (row["group_id"],)
        ).fetchone()
        revision_service = ReportRevisionService(ar)
        current_version = revision_service.current_version(report_kind, report_id)
        revision_payload = row["json_path"]
        if current_version:
            snapshot = revision_service.version(report_kind, report_id, current_version)
            markdown = snapshot['markdown'] if snapshot['markdown'] is not None else "报告版本正文不可读取"
            revision_payload = snapshot['payload'] or {}
        else:
            try:
                markdown = markdown_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                markdown = "报告文件不存在或无法读取，可尝试重新生成修复"
        cfg = _config(request)
        evidence_items = load_verified_report_sources(
            ar.connection, revision_payload, group_id=row["group_id"],
            start_date=start_date, end_date=end_date,
            timezone_name=cfg.summary.timezone if cfg else "Asia/Shanghai",
        )
        return {
            "markdown": markdown,
            "current_version": current_version,
            "report_kind": report_kind,
            "group_name": group["name"] if group else str(row["group_id"]),
            "window_start_date": start_date,
            "window_end_date": end_date,
            "evidence_status": "available" if evidence_items is not None else "legacy",
            "group_id": row["group_id"],
            "evidence_items": evidence_items or [],
            "completeness": ReportCompletenessService(ar).report(
                revision_payload, report_kind, end_date
            ),
        }

    @app.get("/api/reports/{report_kind}/{report_id}/sources/{msg_id}")
    async def api_report_source_context(
        request: Request, report_kind: str, report_id: int, msg_id: str,
        version: int | None = Query(default=None, ge=0),
        direction: str = "around", cursor: str | None = None,
    ):
        require_login(request)
        ar = _archive(request)
        if version is not None:
            try:
                ReportRevisionService(ar).check_version(report_kind, report_id, version)
            except RevisionConflict as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            except LookupError as exc:
                raise HTTPException(404, detail=str(exc)) from exc
        if report_kind == "daily":
            row = ar.connection.execute(
                "SELECT * FROM reports WHERE report_id=?", (report_id,)
            ).fetchone()
            start_date = end_date = row["report_date"] if row else None
        elif report_kind == "range":
            row = ar.manual_report_by_id(report_id)
            start_date = row["start_date"] if row else None
            end_date = row["end_date"] if row else None
        else:
            row = None
            start_date = end_date = None
        if row is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        cfg = _config(request)
        revisions=ReportRevisionService(ar)
        selected_version=version if version is not None else revisions.current_version(report_kind,report_id)
        source_payload=revisions.version(report_kind,report_id,selected_version)['payload'] if selected_version else row['json_path']
        evidence_items = load_verified_report_sources(
            ar.connection, source_payload or {}, group_id=row["group_id"],
            start_date=start_date, end_date=end_date,
            timezone_name=cfg.summary.timezone if cfg else "Asia/Shanghai",
        )
        if not evidence_items or not any(
            msg_id in item["source_ids"] for item in evidence_items
        ):
            raise HTTPException(status_code=404, detail="报告未引用这条消息")
        return await api_search_message_context(request, row["group_id"], msg_id,
                                                direction=direction, cursor=cursor)

    @app.post("/api/reports/{report_kind}/{report_id}/ask")
    async def api_ask_report(
        request: Request, report_kind: str, report_id: int, payload: AskReportPayload
    ):
        require_login(request)
        if payload.expected_version is not None:
            try:
                ReportRevisionService(_archive(request)).check_version(report_kind, report_id, payload.expected_version)
            except RevisionConflict as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            except LookupError as exc:
                raise HTTPException(404, detail=str(exc)) from exc
        cfg = _config(request)
        if cfg is None:
            raise HTTPException(status_code=503, detail="AI 配置不可用")
        try:
            evidence = load_report_evidence(
                _archive(request), report_kind, report_id, ZoneInfo(cfg.summary.timezone)
            )
        except ReportNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except NoReportEvidence as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        def ask_in_worker():
            with app.state.operations.claim("archive_qa"):
                from dataclasses import replace
                from ..attachments import AttachmentService
                prepared_evidence=replace(evidence,messages=AttachmentService(_archive(request),cfg).prepare(evidence.messages))
                selected = getattr(cfg.ai, 'connection_for', lambda _purpose: None)('report_qa')
                if selected is not None:
                    from ..ai.factory import build_ai_client
                    client = build_ai_client(cfg, purpose='report_qa')
                else:
                    client = AIClient(
                        base_url=cfg.resolve_base_url(), api_key=cfg.resolve_api_key(),
                        model=cfg.ai.model, json_mode=True,
                        timeout_seconds=cfg.ai.timeout_seconds, max_retries=2,
                        usage_service=usage_for(cfg), purpose='report_qa')
                try:
                    return answer_report_question(
                        prepared_evidence,
                        payload.question,
                        [turn.model_dump() for turn in payload.history],
                        client,
                        max_chars=min(cfg.ai.max_context_chars, 30000),
                    )
                finally:
                    client.close()
        try:
            return await guarded_to_thread(ask_in_worker)
        except OperationBusy as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except NoReportEvidence as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AILimitExceeded as exc:
            raise HTTPException(429, detail=str(exc)) from exc
        except (AIError, ConfigError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="DeepSeek 问答暂不可用，请检查 API Key、接口和网络",
            ) from exc

    @app.get("/api/candidates")
    async def api_candidates(
        request: Request,
        status: str = "pending",
        group_id: int | None = None,
        group_ids: list[int] | None = Query(None),
        candidate_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        require_login(request)
        try:
            result = candidates.query_page(
                status=status,
                group_id=group_id,
                group_ids=group_ids,
                candidate_type=candidate_type,
                date_from=date_from,
                date_to=date_to,
                q=q,
                page=page,page_size=page_size,
            )
            items=result['items']
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        group_names = {
            group.group_id: group.name for group in _archive(request).all_groups()
        }
        return {
            'total':result['total'],'page':result['page'],'page_size':result['page_size'],
            "candidates": [
                {
                    **item.model_dump(),
                    "group_name": group_names.get(item.group_id, str(item.group_id)),
                }
                for item in items
            ]
        }

    @app.get("/api/candidates/{candidate_id}")
    async def api_candidate_detail(request: Request, candidate_id: int):
        require_login(request)
        try:
            item = candidates.get(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        group = _archive(request).connection.execute(
            "SELECT name FROM groups WHERE group_id=?", (item.group_id,)
        ).fetchone()
        return {
            **item.model_dump(),
            "group_name": group["name"] if group else str(item.group_id),
            **candidate_source_context(_archive(request), item),
        }

    @app.patch("/api/candidates/{candidate_id}")
    async def api_edit_candidate(request: Request, candidate_id: int, payload: CandidateEditPayload):
        require_login(request)
        try:
            updated = candidates.update_details(candidate_id, **payload.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return updated.model_dump()

    async def _undo_candidate(request: Request, candidate_id: int):
        require_login(request)
        try:return knowledge_library.undo_candidate(candidate_id)
        except KeyError as exc:raise HTTPException(404,str(exc)) from exc
        except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc
        except sqlite3.Error as exc:raise HTTPException(503,'知识存储暂时不可用，请稍后重试；原数据已保留') from exc

    @app.post("/api/candidates/{candidate_id}/undo")
    async def api_undo_candidate(request: Request, candidate_id: int):
        require_login(request)
        try:
            with operations.claim('knowledge_mutation'):
                return await _undo_candidate(request, candidate_id)
        except OperationBusy as exc:
            raise HTTPException(409, detail='已有任务在运行，请完成后重试') from exc

    # ------------------------------------------------------------------
    # API: Run daily
    # ------------------------------------------------------------------
    @app.post("/api/run-daily")
    async def api_run_daily(request: Request):
        require_login(request)
        cfg = _config(request)
        if not cfg:
            raise HTTPException(status_code=500, detail="配置不可用")
        try:
            with operations.claim("daily"):
                scheduler_state["running"] = True
                try:
                    result = await guarded_to_thread(_run_daily_task, cfg)
                    scheduler_state["last_result"] = result
                    scheduler_state["last_attempt_date"] = datetime.now(
                        ZoneInfo(cfg.summary.timezone)
                    ).date().isoformat()
                    if result.get("status") == "failed":
                        raise HTTPException(
                            status_code=500,
                            detail=result.get("error", "摘要任务失败"),
                        )
                    if result.get("status") == "success":
                        scheduler_state["last_run_date"] = datetime.now(
                            ZoneInfo(cfg.summary.timezone)
                        ).date().isoformat()
                    return {
                        key: value
                        for key, value in result.items()
                        if key != "success"
                    }
                finally:
                    scheduler_state["running"] = False
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc

    # ------------------------------------------------------------------
    # Candidate actions (existing)
    # ------------------------------------------------------------------
    @app.post("/api/candidates/{candidate_id}/confirm")
    @app.post("/candidates/{candidate_id}/confirm")
    async def confirm(request: Request, candidate_id: int):
        require_login(request)
        try:
            with operations.claim('knowledge_mutation'):
                knowledge_library.confirm_candidate(candidate_id)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(503,'知识存储暂时不可用，请稍后重试；原数据已保留') from exc
        except KeyError as exc:
            raise HTTPException(404,'候选不存在') from exc
        except OperationBusy as exc:
            raise HTTPException(409,'后台任务正在运行，请稍后重试入库') from exc
        except OSError as exc:
            raise HTTPException(503,'知识写入失败，候选和原数据已保留') from exc
        if request.url.path.startswith('/api/'):
            return {"status": "confirmed"}
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/ignore")
    async def ignore(request: Request, candidate_id: int, reason: str = Form("")):
        require_login(request)
        try:
            with operations.claim('knowledge_mutation'):
                candidates.ignore(candidate_id, reason)
        except KeyError as exc:
            raise HTTPException(404, '候选不存在') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OperationBusy as exc:
            raise HTTPException(409, '后台任务正在运行，请稍后重试') from exc
        except sqlite3.Error as exc:
            raise HTTPException(503, '知识存储暂时不可用，请稍后重试；原数据已保留') from exc
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/api/candidates/{candidate_id}/later")
    @app.post("/candidates/{candidate_id}/later")
    async def later(request: Request, candidate_id: int):
        require_login(request)
        try:
            with operations.claim('knowledge_mutation'):
                if candidates.get(candidate_id).status != 'pending':
                    raise HTTPException(409, '候选状态已变化，请刷新后核对')
                candidates.update_status(candidate_id, "later")
        except KeyError as exc:
            raise HTTPException(404, '候选不存在') from exc
        except OperationBusy as exc:
            raise HTTPException(409, '后台任务正在运行，请稍后重试') from exc
        if request.url.path.startswith('/api/'):
            return {"status": "later"}
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/api/candidates/{candidate_id}/restore")
    @app.post("/candidates/{candidate_id}/restore")
    async def restore(request: Request, candidate_id: int):
        require_login(request)
        try:
            with operations.claim('knowledge_mutation'):
                if candidates.get(candidate_id).status != 'later':
                    raise HTTPException(409, '候选状态已变化，请刷新后核对')
                candidates.update_status(candidate_id, "pending")
        except KeyError as exc:
            raise HTTPException(404, '候选不存在') from exc
        except OperationBusy as exc:
            raise HTTPException(409, '后台任务正在运行，请稍后重试') from exc
        if request.url.path.startswith('/api/'):
            return {"status": "pending"}
        return RedirectResponse("/candidates", status_code=303)

    from .storage_handoff import add_storage_handoff
    add_storage_handoff(app, require_login=require_login)
    return app
