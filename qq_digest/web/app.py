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

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from ..archive import Archive
from ..ai.client import AIClient, AIError
from ..ai.key_store import save_ui_api_key, ui_api_key_exists
from ..candidates import CandidateService
from ..collector.ntqq import NTQQCollector
from ..config import Config, ConfigError, load_config
from ..knowledge import KnowledgeItem, KnowledgeWriter
from ..scheduler import daily_retry_state
from ..report_qa import (
    NoReportEvidence, ReportNotFound, answer_report_question, load_report_evidence,
)
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
            ORDER BY job_id DESC
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
            if succeeded:
                scheduler_state["last_run_date"] = attempt_date
            scheduler_state["last_result"] = {
                "status": latest_daily_job["status"],
                "success": succeeded,
                "error": latest_daily_job["error"] or "" if not succeeded else "",
            }
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

    def _run_daily_task(cfg):
        """Synchronous daily pipeline: refresh DB then run summaries."""
        import logging
        scheduler_logger = logging.getLogger("qq_digest.scheduler")
        worker_archive = None
        ai_client = None
        pipeline_started = False

        def record_preflight_failure(error: str) -> None:
            failure_archive = Archive.open(cfg.archive_path)
            try:
                job_id = failure_archive.start_job("daily_digest")
                failure_archive.finish_job(job_id, "failed", error)
            finally:
                failure_archive.close()

        try:
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
            from ..ai.factory import build_ai_client
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
            ai_client = build_ai_client(cfg)
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
            result = pipeline.run_daily(datetime.now(ZoneInfo(cfg.summary.timezone)))
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
            retry = daily_retry_state(
                archive.connection,
                now,
                target_hour=config.summary.hour,
                target_minute=config.summary.minute,
                max_attempts=config.summary.max_attempts,
                retry_interval_minutes=config.summary.retry_interval_minutes,
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
                "SELECT last_timestamp FROM sync_state WHERE group_id=?",
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
                "SELECT last_timestamp FROM sync_state WHERE group_id=?", (g.group_id,)
            ).fetchone()
            result.append({"name": g.name, "message_count": cnt,
                           "last_sync": _utc(sync["last_timestamp"]) if sync and sync["last_timestamp"] else "未同步"})
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
        return {"groups": [{"group_id": g["group_id"], "name": g["name"],
                            "message_count_30d": g["message_count_30d"],
                            "latest_message_at": g["latest_message_at"].isoformat() if g["latest_message_at"] else None}
                           for g in groups]}

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
        if not gid or not start_str or not end_str:
            raise HTTPException(status_code=400, detail="缺少参数")
        cfg = _config(request)
        tz = ZoneInfo(cfg.summary.timezone if cfg else "Asia/Shanghai")
        start = datetime.fromisoformat(start_str + "T00:00:00").replace(tzinfo=tz)
        end = datetime.fromisoformat(end_str + "T23:59:59").replace(tzinfo=tz)
        if not cfg or not cfg.ntqq.enabled:
            raise HTTPException(status_code=400, detail="NTQQ 未启用")

        def collect_in_worker():
            collector = NTQQCollector(
                db_dir=cfg.ntqq.db_dir,
                qq_number=cfg.ntqq.qq_number,
                timezone_name=cfg.ntqq.timezone,
            )
            msgs = list(collector.collect(gid, start, end))
            worker_archive = Archive.open(cfg.archive_path)
            try:
                ingest = worker_archive.ingest(msgs)
                worker_archive.mark_sync(group_id=gid, last_timestamp=min(end, datetime.now(tz)))
                worker_archive.connection.commit()
            finally:
                worker_archive.connection.close()
            return msgs, ingest

        try:
            with operations.claim("collect"):
                msgs, ingest = await asyncio.to_thread(collect_in_worker)
        except OperationBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "已有冲突任务在运行", "active": exc.active},
            ) from exc

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
                for m in preview
            ],
            "total": len(msgs),
            "inserted": ingest.inserted,
            "skipped": ingest.skipped,
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
    async def api_reports(request: Request):
        require_login(request)
        ar = _archive(request)
        daily_rows = ar.connection.execute(
            """SELECT r.*, g.name AS group_name FROM reports r
               JOIN groups g ON g.group_id = r.group_id
               ORDER BY r.report_date DESC, g.name LIMIT 100"""
        ).fetchall()
        range_rows = ar.connection.execute(
            """
            SELECT r.*, g.name AS group_name FROM manual_reports r
            JOIN groups g ON g.group_id = r.group_id
            ORDER BY r.end_date DESC, r.start_date DESC, g.name LIMIT 100
            """
        ).fetchall()
        reports = [
            {
                "report_kind": "daily",
                "report_id": int(row["report_id"]),
                "report_key": f"daily:{row['report_id']}",
                "report_date": row["report_date"],
                "window_start_date": row["report_date"],
                "window_end_date": row["report_date"],
                "group_name": row["group_name"],
                "_json_path": row["json_path"],
                "candidate_count": len(json.loads(row["candidate_ids"])),
                "created_at": _utc(row["created_at"]),
            }
            for row in daily_rows
        ]
        reports.extend(
            {
                "report_kind": "range",
                "report_id": int(row["manual_report_id"]),
                "report_key": f"range:{row['manual_report_id']}",
                "report_date": row["end_date"],
                "window_start_date": row["start_date"],
                "window_end_date": row["end_date"],
                "group_name": row["group_name"],
                "_json_path": row["json_path"],
                "candidate_count": len(json.loads(row["candidate_ids"])),
                "created_at": _utc(row["updated_at"]),
            }
            for row in range_rows
        )
        reports.sort(
            key=lambda row: (
                row["window_end_date"],
                row["window_start_date"],
                row["created_at"],
            ),
            reverse=True,
        )
        selected_reports = reports[:100]
        for report in selected_reports:
            report["reference_message_count"] = _reference_message_count(
                report.pop("_json_path")
            )
        return {"reports": selected_reports}

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
        return {"markdown": markdown, "report_date": row["report_date"]}

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
        markdown = (
            markdown_path.read_text(encoding="utf-8")
            if markdown_path.exists()
            else "报告文件不存在"
        )
        return {
            "markdown": markdown,
            "report_kind": report_kind,
            "window_start_date": start_date,
            "window_end_date": end_date,
        }

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
        }

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
