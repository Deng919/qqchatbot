from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from ..archive import Archive, IngestResult
from ..ai.client import AIClient, AIError
from ..ai.key_store import save_ui_api_key, ui_api_key_exists
from ..candidates import CandidateService
from ..catchup import CatchupService
from ..failure_center import FailureCenterService
from ..report_completeness import ReportCompletenessService
from ..task_inbox import TaskInboxService
from ..candidate_context import candidate_source_context
from ..collector.ntqq import NTQQCollector
from ..config import Config, ConfigError, load_config
from ..knowledge import KnowledgeItem, KnowledgeWriter, remove_item
from ..scheduler import (
    daily_retry_state, pending_catchup_date, run_at_for_report_date, summary_window,
)
from ..search import search_archive
from ..report_qa import (
    NoReportEvidence, ReportNotFound, answer_report_question, load_report_evidence,
)
from ..report_sources import load_verified_report_sources
from .auth import PasswordHasher, SessionCookie
from .operations import OperationBusy, OperationCoordinator

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class ManualRangePayload(BaseModel):
    group_ids: list[int]
    start_date: date
    end_date: date
    detail_mode: str | None = None


class AIKeyPayload(BaseModel):
    api_key: str


class CandidateEditPayload(BaseModel):
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


class QATurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskReportPayload(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[QATurn] = Field(default_factory=list, max_length=6)

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


class CatchupReadPayload(BaseModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    read: bool


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
    status: Literal["open", "completed", "canceled"]


def require_login(request: Request):
    cookie = request.app.state.cookie
    if not cookie.verify(request.cookies.get("qq_digest_session")):
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="未登录")
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def _config(request: Request) -> Config:
    return request.app.state.config


def _archive(request: Request) -> Archive:
    return request.app.state.archive


def _desktop_bridge(request: Request):
    require_login(request)
    bridge = getattr(request.app.state, "desktop_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="请启动 QQ Digest 桌面程序后刷新设置页")
    return bridge


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
    )
    try:
        response = client.chat(
            [
                {"role": "system", "content": "Respond with a JSON object only."},
                {"role": "user", "content": 'Connection test. Respond with JSON {"ok": true}.'},
            ]
        )
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise AIError("DeepSeek 连接测试未得到预期响应")
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
):
    # QQ Bot WebSocket listener state
    ws_state = {"listener": None, "task": None}
    notification_state = {"notifier": None, "task": None}
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
        """Synchronous daily pipeline: refresh DB then run summaries."""
        import logging
        scheduler_logger = logging.getLogger("qq_digest.scheduler")
        worker_archive = None
        ai_client = None
        pipeline_started = False
        scheduled_at = run_at or datetime.now(ZoneInfo(cfg.summary.timezone))
        target_date = summary_window(
            scheduled_at, cfg.summary.window_mode, ZoneInfo(cfg.summary.timezone)
        )[0].date().isoformat()

        def record_preflight_failure(error: str) -> None:
            failure_archive = Archive.open(cfg.archive_path)
            try:
                job_id = failure_archive.start_job(
                    "daily_digest", target_date=target_date,
                    retry_of_job_id=retry_of_job_id,
                )
                failure_archive.finish_job(job_id, "failed", error)
            finally:
                failure_archive.close()

        try:
            from ..ai.factory import build_ai_client
            ai_client = build_ai_client(cfg)

            # Step 1: Refresh NTQQ database
            if cfg.ntqq.enabled and cfg.ntqq.db_dir:
                scheduler_logger.info("定时任务: 刷新 NTQQ 数据库...")
                from ..refresh import refresh_database
                refresh_result = refresh_database(
                    qq_number=cfg.ntqq.qq_number,
                    output_dir=cfg.ntqq.db_dir,
                    snapshot_root=cfg.work_dir / "snapshots",
                )
                scheduler_logger.info("定时任务刷新: %s", refresh_result.message)
                if not refresh_result.success:
                    error = f"数据库刷新失败: {refresh_result.message}"
                    record_preflight_failure(error)
                    return {
                        "success": False,
                        "status": "failed",
                        "error": error,
                    }

            # Step 2: Create fresh collector (picks up new DB)
            from ..collector.ntqq import NTQQCollector
            from ..collector.fixture import FixtureCollector
            from ..pipeline import DailyPipeline
            from ..notify import BotConfig

            if cfg.ntqq.enabled and cfg.ntqq.db_dir:
                collector = NTQQCollector(
                    db_dir=cfg.ntqq.db_dir,
                    qq_number=cfg.ntqq.qq_number,
                    timezone_name=cfg.ntqq.timezone,
                )
            else:
                collector = FixtureCollector()

            # Step 3: Run daily pipeline
            worker_archive = Archive.open(cfg.archive_path)
            pipeline = DailyPipeline(
                archive=worker_archive,
                collector=collector,
                ai_client=ai_client,
                report_dir=cfg.report_dir,
                max_context_chars=cfg.ai.max_context_chars,
                timezone_name=cfg.summary.timezone,
                window_mode=cfg.summary.window_mode,
                knowledge_paths=cfg.resolve_knowledge_paths(),
                bot_config=(
                    BotConfig(
                        app_id=cfg.qq_bot.app_id,
                        app_secret=os.environ.get(cfg.qq_bot.app_secret_env, ""),
                        allowed_openids=cfg.qq_bot.allowed_openids,
                        api_base=cfg.qq_bot.api_base,
                        websocket_url=cfg.qq_bot.websocket_url,
                        max_retries=cfg.qq_bot.max_retries,
                        retry_base_seconds=cfg.qq_bot.retry_base_seconds,
                    )
                    if cfg.qq_bot.enabled and cfg.qq_bot.app_id
                    else None
                ),
            )
            pipeline_started = True
            result = pipeline.run_daily(
                scheduled_at, retry_of_job_id=retry_of_job_id
            )
            scheduler_logger.info(
                "定时任务完成: %d 群, %d 消息, %d 报告, %d 候选",
                result.groups_processed, result.messages_inserted,
                len(result.report_paths), len(result.candidate_ids),
            )
            return {
                "success": result.status == "success",
                "status": result.status,
                "groups_processed": result.groups_processed,
                "messages_inserted": result.messages_inserted,
                "reports": len(result.report_paths),
                "candidates": len(result.candidate_ids),
                "succeeded_groups": result.succeeded_groups,
                "skipped_groups": result.skipped_groups,
                "failed_groups": [asdict(item) for item in result.failed_groups],
            }
        except Exception as exc:
            scheduler_logger.error("定时任务失败: %s", exc, exc_info=True)
            if not pipeline_started:
                record_preflight_failure(str(exc))
            return {"success": False, "status": "failed", "error": str(exc)}
        finally:
            if ai_client is not None:
                ai_client.close()
            if worker_archive is not None:
                worker_archive.connection.close()

    def _run_manual_summary_task(cfg, payload: ManualRangePayload):
        from ..ai.factory import build_ai_client
        from ..manual_summary import ManualSummaryRequest, ManualSummaryService

        worker_archive = Archive.open(cfg.archive_path)
        ai_client = None
        try:
            ai_client = build_ai_client(cfg)
            service = ManualSummaryService(
                archive=worker_archive,
                ai_client=ai_client,
                report_dir=cfg.report_dir,
                max_context_chars=cfg.ai.max_context_chars,
                timezone_name=cfg.summary.timezone,
                knowledge_paths=cfg.resolve_knowledge_paths(),
            )
            result = service.run(
                ManualSummaryRequest(
                    group_ids=payload.group_ids,
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                )
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

    async def _daily_scheduler_loop():
        """Check every 60s if it's time to run the daily task."""
        import logging
        scheduler_logger = logging.getLogger("qq_digest.scheduler")
        while True:
            await asyncio.sleep(60)
            if (
                not config
                or scheduler_state["running"]
                or not operations.can_start("daily")
            ):
                continue

            tz = ZoneInfo(config.summary.timezone)
            now = datetime.now(tz)
            today = now.date().isoformat()
            catchup_date = pending_catchup_date(
                archive.connection, now,
                mode=config.summary.window_mode,
                max_attempts=config.summary.max_attempts,
                retry_interval_minutes=config.summary.retry_interval_minutes,
            )
            if catchup_date is not None:
                scheduler_logger.info("补生成遗漏日报: %s", catchup_date)
                try:
                    with operations.claim("daily"):
                        scheduler_state["running"] = True
                        scheduler_state["last_attempt_date"] = catchup_date.isoformat()
                        try:
                            catchup_result = await asyncio.to_thread(
                                _run_daily_task, config,
                                run_at_for_report_date(
                                    catchup_date, config.summary.window_mode, tz
                                ),
                            )
                            latest_report = archive.connection.execute(
                                """SELECT target_date FROM jobs WHERE job_type='daily_digest'
                                   ORDER BY COALESCE(target_date, substr(started_at, 1, 10)) DESC,
                                            job_id DESC LIMIT 1"""
                            ).fetchone()
                            if latest_report and latest_report["target_date"] == catchup_date.isoformat():
                                scheduler_state["last_result"] = catchup_result
                        finally:
                            scheduler_state["running"] = False
                except OperationBusy:
                    pass
                continue

            scheduled_target = summary_window(
                now, config.summary.window_mode, tz
            )[0].date().isoformat()
            retry = daily_retry_state(
                archive.connection,
                now,
                target_hour=config.summary.hour,
                target_minute=config.summary.minute,
                max_attempts=config.summary.max_attempts,
                retry_interval_minutes=config.summary.retry_interval_minutes,
                target_date=scheduled_target,
            )
            if not retry.due:
                continue

            # Time to run
            scheduler_logger.info(
                "定时任务触发: %s，第 %d/%d 次",
                today,
                retry.attempts + 1,
                config.summary.max_attempts,
            )
            scheduler_state["last_attempt_date"] = today
            try:
                with operations.claim("daily"):
                    scheduler_state["running"] = True
                    try:
                        result = await asyncio.to_thread(_run_daily_task, config)
                        scheduler_state["last_result"] = result
                        if result.get("success"):
                            scheduler_state["last_run_date"] = today
                    finally:
                        scheduler_state["running"] = False
            except OperationBusy:
                continue

    async def _notification_queue_loop(notifier):
        import logging
        from ..notify import NotificationQueueProcessor

        while True:
            queue_archive = None
            try:
                queue_archive = Archive.open(config.archive_path)
                await asyncio.to_thread(
                    NotificationQueueProcessor(
                        archive=queue_archive,
                        notifier=notifier,
                        max_attempts=config.qq_bot.max_retries,
                        retry_base_seconds=config.qq_bot.retry_base_seconds,
                    ).process_due
                )
            except Exception:
                logging.getLogger("qq_digest.notify").exception(
                    "通知队列处理失败，将在下一周期重试"
                )
            finally:
                if queue_archive is not None:
                    queue_archive.close()
            await asyncio.sleep(60)

    async def _message_sync_loop():
        if not config:
            return
        await asyncio.sleep(config.collection.startup_delay_seconds)
        while True:
            if (
                config.collection.enabled
                and config.ntqq.enabled
                and operations.can_start("sync")
            ):
                from ..sync import run_message_sync

                try:
                    with operations.claim("sync"):
                        sync_scheduler_state["running"] = True
                        try:
                            result = await asyncio.to_thread(
                                run_message_sync, config=config
                            )
                            sync_scheduler_state["last_result"] = result.to_dict()
                            sync_scheduler_state["last_run_at"] = datetime.now(
                                ZoneInfo(config.summary.timezone)
                            ).strftime("%Y-%m-%d %H:%M")
                        finally:
                            sync_scheduler_state["running"] = False
                except OperationBusy:
                    pass
            await asyncio.sleep(config.collection.interval_minutes * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: launch WebSocket listener if QQ Bot is configured
        cfg = config
        if cfg and cfg.qq_bot.enabled and cfg.qq_bot.app_id:
            from ..notify import QQBotNotifier, BotConfig, BotCommandHandler, WebSocketListener

            bot_config = BotConfig(
                app_id=cfg.qq_bot.app_id,
                app_secret=os.environ.get(cfg.qq_bot.app_secret_env, ""),
                allowed_openids=cfg.qq_bot.allowed_openids,
                api_base=cfg.qq_bot.api_base,
                websocket_url=cfg.qq_bot.websocket_url,
                max_retries=cfg.qq_bot.max_retries,
                retry_base_seconds=cfg.qq_bot.retry_base_seconds,
            )
            notifier = QQBotNotifier(bot_config)
            handler = BotCommandHandler(
                notifier=notifier,
                candidates=candidates,
                knowledge=knowledge,
                group_names={g.group_id: g.name for g in archive.enabled_groups()},
            )
            ws_state["listener"] = WebSocketListener(notifier=notifier, handler=handler)
            ws_state["task"] = asyncio.get_event_loop().create_task(ws_state["listener"].start())
            notification_state["notifier"] = notifier
            notification_state["task"] = asyncio.get_event_loop().create_task(
                _notification_queue_loop(notifier)
            )

        # Start daily scheduler
        scheduler_state["task"] = asyncio.get_event_loop().create_task(_daily_scheduler_loop())
        sync_scheduler_state["task"] = asyncio.get_event_loop().create_task(
            _message_sync_loop()
        )

        yield

        # Shutdown: stop listener
        if ws_state["listener"]:
            await ws_state["listener"].stop()
        background_tasks = [
            task
            for task in (
                ws_state["task"],
                notification_state["task"],
                scheduler_state["task"],
                sync_scheduler_state["task"],
            )
            if task is not None
        ]
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    app = FastAPI(title="QQ Digest", lifespan=lifespan)
    cookie = SessionCookie(session_secret, session_hours * 3600)
    app.state.archive = archive
    app.state.candidates = candidates
    app.state.knowledge = knowledge
    app.state.password_hash = password_hash
    app.state.cookie = cookie
    app.state.config = config
    app.state.operations = operations
    app.state.desktop_bridge = None

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
        return templates.TemplateResponse(request, "login.html", {})

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if not PasswordHasher.verify(password, app.state.password_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "密码错误"}, status_code=401
            )
        response = RedirectResponse("/", status_code=303)
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
        return templates.TemplateResponse(request, "catchup.html", {})

    def catchup_service() -> CatchupService:
        return CatchupService(archive, timezone_name=(
            config.summary.timezone if config is not None else "Asia/Shanghai"
        ))

    @app.post("/api/catchup/visit")
    async def api_catchup_visit(request: Request):
        require_login(request)
        return catchup_service().visit()

    @app.get("/api/catchup")
    async def api_catchup(
        request: Request, scope: Literal["since", "today", "week"] = "since",
        since: str | None = None, page: int = Query(1, ge=1),
    ):
        require_login(request)
        try:
            return await asyncio.to_thread(
                catchup_service().list_items, scope, since=since, page=page
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/catchup/read")
    async def api_catchup_read(request: Request, payload: CatchupReadPayload):
        require_login(request)
        catchup_service().set_read(payload.key, payload.read)
        return {"key": payload.key, "read": payload.read}

    @app.get("/tasks")
    async def task_inbox_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "tasks.html", {})

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
                        result = await asyncio.to_thread(
                            _run_daily_task, cfg, run_at, job_id
                        )
                    finally:
                        scheduler_state["running"] = False
                    status = result.get("status", "failed")
                else:
                    from ..sync import run_message_sync
                    sync_scheduler_state["running"] = True
                    try:
                        result = await asyncio.to_thread(
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
            task = await asyncio.to_thread(
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
    async def api_task_update(request: Request, task_id: int, payload: TaskUpdatePayload):
        require_login(request)
        try:
            task = task_inbox_service().update_task(
                task_id, title=payload.title, owner=payload.owner,
                due_date=payload.due_date, status=payload.status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task}

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
        return templates.TemplateResponse(request, "ai_settings.html", {})

    @app.get("/settings")
    async def settings_page(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "settings.html", {})

    @app.get("/api/desktop-settings")
    async def desktop_settings_state(request: Request):
        bridge = _desktop_bridge(request)
        return await asyncio.to_thread(bridge.get_settings)

    @app.post("/api/desktop-settings/choose-folder")
    async def desktop_choose_folder(request: Request, payload: FolderSelectionPayload):
        bridge = _desktop_bridge(request)
        try:
            return {"path": await asyncio.to_thread(bridge.choose_folder, payload.kind)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"文件夹窗口打开失败：{exc}") from exc

    @app.post("/api/desktop-settings/migrate")
    async def desktop_migrate(request: Request, payload: StorageMigrationPayload):
        bridge = _desktop_bridge(request)
        try:
            return await asyncio.to_thread(bridge.migrate_storage, payload.destination)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/export")
    async def desktop_export(request: Request, payload: ReportExportPayload):
        bridge = _desktop_bridge(request)
        return await asyncio.to_thread(bridge.export_reports, payload.destination, payload.format)

    @app.post("/api/desktop-settings/auto-start")
    async def desktop_auto_start(request: Request, payload: AutoStartPayload):
        bridge = _desktop_bridge(request)
        return await asyncio.to_thread(bridge.set_auto_start, payload.enabled)

    @app.post("/api/desktop-settings/backup")
    async def desktop_backup(request: Request):
        bridge = _desktop_bridge(request)
        try:
            return await asyncio.to_thread(bridge.backup_data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/backup-schedule")
    async def desktop_backup_schedule(request: Request, payload: BackupSchedulePayload):
        bridge = _desktop_bridge(request)
        return await asyncio.to_thread(bridge.set_backup_schedule, payload.schedule)

    @app.post("/api/desktop-settings/choose-backup")
    async def desktop_choose_backup(request: Request):
        bridge = _desktop_bridge(request)
        try:
            return {"path": await asyncio.to_thread(bridge.choose_backup_file)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"备份文件窗口打开失败：{exc}") from exc

    @app.post("/api/desktop-settings/restore-preview")
    async def desktop_restore_preview(request: Request, payload: RestorePreviewPayload):
        bridge = _desktop_bridge(request)
        try:
            return await asyncio.to_thread(bridge.preview_restore, payload.path)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/restore")
    async def desktop_restore(request: Request, payload: RestorePayload):
        bridge = _desktop_bridge(request)
        try:
            return await asyncio.to_thread(bridge.restore_backup, payload.path,
                                           payload.destination, payload.sha256)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desktop-settings/open-folder")
    async def desktop_open_folder(request: Request, payload: OpenFolderPayload):
        bridge = _desktop_bridge(request)
        return await asyncio.to_thread(bridge.open_folder, payload.kind)

    # ------------------------------------------------------------------
    # API: DeepSeek settings
    # ------------------------------------------------------------------
    @app.get("/api/ai-settings")
    async def api_ai_settings(request: Request):
        require_login(request)
        cfg = _config(request)
        return {
            "base_url": cfg.ai.base_url,
            "model": cfg.ai.model,
            "provider_priority": cfg.ai.provider_priority,
            "bridge_model": cfg.ai.bridge_model,
            "key_configured": bool(
                cfg.ai.ui_api_key_file
                and ui_api_key_exists(Path(cfg.ai.ui_api_key_file))
            ),
        }

    @app.put("/api/ai-settings/key")
    async def api_save_ai_key(request: Request, payload: AIKeyPayload):
        require_login(request)
        cfg = _config(request)
        if not cfg.ai.ui_api_key_file:
            raise HTTPException(status_code=503, detail="本地 API Key 保存路径未配置")
        try:
            await asyncio.to_thread(
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
            await asyncio.to_thread(_test_deepseek_connection, cfg)
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

    @app.get("/api/search")
    async def api_search(
        request: Request, q: str = "",
        kind: Literal["all", "message", "report", "knowledge"] = "all",
        group_id: int | None = None,
        date_from: date | None = None, date_to: date | None = None,
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    ):
        require_login(request)
        try:
            return search_archive(
                _archive(request), query=q, kind=kind, group_id=group_id,
                date_from=date_from, date_to=date_to, page=page, page_size=page_size,
                timezone_name=_config(request).summary.timezone if _config(request) else "Asia/Shanghai",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/search/messages/{group_id}/{msg_id}/context")
    async def api_search_message_context(request: Request, group_id: int, msg_id: str):
        require_login(request)
        ar = _archive(request)
        message = ar.connection.execute(
            "SELECT timestamp FROM messages WHERE group_id=? AND msg_id=?",
            (group_id, msg_id),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        before = ar.connection.execute(
            "SELECT msg_id, sender_qq, timestamp, text FROM messages "
            "WHERE group_id=? AND (timestamp < ? OR (timestamp=? AND msg_id < ?)) "
            "ORDER BY timestamp DESC, msg_id DESC LIMIT 2",
            (group_id, message["timestamp"], message["timestamp"], msg_id),
        ).fetchall()
        after = ar.connection.execute(
            "SELECT msg_id, sender_qq, timestamp, text FROM messages "
            "WHERE group_id=? AND (timestamp > ? OR (timestamp=? AND msg_id > ?)) "
            "ORDER BY timestamp, msg_id LIMIT 2",
            (group_id, message["timestamp"], message["timestamp"], msg_id),
        ).fetchall()
        current = ar.connection.execute(
            "SELECT msg_id, sender_qq, timestamp, text FROM messages WHERE group_id=? AND msg_id=?",
            (group_id, msg_id),
        ).fetchone()
        return {"messages": [dict(row) for row in [*reversed(before), current, *after]]}

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
            groups = await asyncio.to_thread(collector.discover_groups)
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
    async def api_update_group(request: Request, group_id: int):
        require_login(request)
        body = await request.json()
        ar = _archive(request)
        allowed = {
            "enabled",
            "daily_summary",
            "category",
            "important_candidates",
            "keywords",
            "collection_window_days",
        }
        sets = []
        vals = []
        for k, v in body.items():
            if k in allowed:
                sets.append(f"{k}=?")
                if k in ("enabled", "daily_summary", "important_candidates"):
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
        vals.append(group_id)
        with ar.transaction():
            ar.connection.execute(f"UPDATE groups SET {', '.join(sets)} WHERE group_id=?", vals)
        return {"ok": True}

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
                msgs, ingest, missing_days = await asyncio.to_thread(collect_in_worker)
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
            "enabled": True,
            "schedule": f"{cfg.summary.hour:02d}:{cfg.summary.minute:02d}" if cfg else "",
            "timezone": cfg.summary.timezone if cfg else "Asia/Shanghai",
            "running": scheduler_state["running"],
            "last_run_date": scheduler_state["last_run_date"],
            "last_result": scheduler_state["last_result"],
            "next_check": "within 60s",
            "collection": {
                "enabled": bool(cfg and cfg.collection.enabled and cfg.ntqq.enabled),
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
                result = await asyncio.to_thread(
                    lambda: refresh_database(
                        qq_number=cfg.ntqq.qq_number,
                        output_dir=cfg.ntqq.db_dir,
                        snapshot_root=cfg.work_dir / "snapshots",
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
        kind: Literal["all", "daily", "range"] = "all",
        date_from: date | None = None,
        date_to: date | None = None,
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
        if group_id is not None:
            clauses.append("group_id=?")
            params.append(group_id)
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
                    return await asyncio.to_thread(
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
        markdown = (
            markdown_path.read_text(encoding="utf-8")
            if markdown_path.exists()
            else "报告文件不存在"
        )
        cfg = _config(request)
        evidence_items = load_verified_report_sources(
            ar.connection, row["json_path"], group_id=row["group_id"],
            start_date=start_date, end_date=end_date,
            timezone_name=cfg.summary.timezone if cfg else "Asia/Shanghai",
        )
        return {
            "markdown": markdown,
            "report_kind": report_kind,
            "group_name": group["name"] if group else str(row["group_id"]),
            "window_start_date": start_date,
            "window_end_date": end_date,
            "evidence_status": "available" if evidence_items is not None else "legacy",
            "evidence_items": evidence_items or [],
            "completeness": ReportCompletenessService(ar).report(
                row["json_path"], report_kind, end_date
            ),
        }

    @app.get("/api/reports/{report_kind}/{report_id}/sources/{msg_id}")
    async def api_report_source_context(
        request: Request, report_kind: str, report_id: int, msg_id: str
    ):
        require_login(request)
        ar = _archive(request)
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
        evidence_items = load_verified_report_sources(
            ar.connection, row["json_path"], group_id=row["group_id"],
            start_date=start_date, end_date=end_date,
            timezone_name=cfg.summary.timezone if cfg else "Asia/Shanghai",
        )
        if not evidence_items or not any(
            msg_id in item["source_ids"] for item in evidence_items
        ):
            raise HTTPException(status_code=404, detail="报告未引用这条消息")
        return await api_search_message_context(request, row["group_id"], msg_id)

    @app.post("/api/reports/{report_kind}/{report_id}/ask")
    async def api_ask_report(
        request: Request, report_kind: str, report_id: int, payload: AskReportPayload
    ):
        require_login(request)
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
            client = AIClient(
                base_url=cfg.resolve_base_url(),
                api_key=cfg.resolve_api_key(),
                model=cfg.ai.model,
                json_mode=True,
                timeout_seconds=cfg.ai.timeout_seconds,
                max_retries=2,
            )
            try:
                return answer_report_question(
                    evidence,
                    payload.question,
                    [turn.model_dump() for turn in payload.history],
                    client,
                    max_chars=min(cfg.ai.max_context_chars, 30000),
                )
            finally:
                client.close()

        try:
            return await asyncio.to_thread(ask_in_worker)
        except NoReportEvidence as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        candidate_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str = "",
    ):
        require_login(request)
        try:
            items = candidates.query(
                status=status,
                group_id=group_id,
                candidate_type=candidate_type,
                date_from=date_from,
                date_to=date_to,
                q=q,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        group_names = {
            group.group_id: group.name for group in _archive(request).all_groups()
        }
        return {
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

    @app.post("/api/candidates/{candidate_id}/undo")
    async def api_undo_candidate(request: Request, candidate_id: int):
        require_login(request)
        try:
            item = candidates.get(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if item.status != "confirmed":
            raise HTTPException(status_code=409, detail="只有已入库的候选可以撤销")
        ar = _archive(request)
        row = ar.connection.execute(
            "SELECT markdown_path FROM knowledge_items WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="找不到对应的知识库条目")
        path = Path(row["markdown_path"])
        try:
            original = remove_item(path, str(candidate_id))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            with ar.transaction():
                ar.connection.execute(
                    "DELETE FROM knowledge_items WHERE candidate_id=?", (candidate_id,)
                )
                ar.connection.execute(
                    "UPDATE candidates SET status='pending', updated_at=? WHERE candidate_id=?",
                    (datetime.now(timezone.utc).isoformat(), candidate_id),
                )
        except Exception:
            path.write_text(original, encoding="utf-8")
            raise
        return {"status": "pending"}

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
                    result = await asyncio.to_thread(_run_daily_task, cfg)
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
    @app.post("/candidates/{candidate_id}/confirm")
    async def confirm(request: Request, candidate_id: int):
        require_login(request)
        candidate = candidates.get(candidate_id)
        group_names = {
            group.group_id: group.name for group in _archive(request).enabled_groups()
        }
        knowledge.write(
            KnowledgeItem(
                item_id=str(candidate_id),
                date=candidate.created_date,
                category="资源" if candidate.candidate_type == "resource" else "经验",
                source_group=group_names.get(candidate.group_id, str(candidate.group_id)),
                title=candidate.title,
                link=candidate.link,
                value=candidate.reason,
                excerpt=candidate.excerpt,
                content=candidate.content,
            ),
            candidate.candidate_type,
        )
        archive.record_knowledge_item(
            item_id=str(candidate_id),
            candidate_id=candidate_id,
            markdown_path=str(knowledge.path_for(candidate.candidate_type)),
        )
        candidates.confirm(candidate_id)
        archive.connection.commit()
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/ignore")
    async def ignore(request: Request, candidate_id: int, reason: str = Form("")):
        require_login(request)
        candidates.ignore(candidate_id, reason)
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/later")
    async def later(request: Request, candidate_id: int):
        require_login(request)
        candidates.update_status(candidate_id, "later")
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/restore")
    async def restore(request: Request, candidate_id: int):
        require_login(request)
        candidates.update_status(candidate_id, "pending")
        return RedirectResponse("/candidates", status_code=303)

    return app
