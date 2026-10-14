"""Periodic work and startup/shutdown orchestration for the Web application."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from ..first_use import setup_in_progress
from ..scheduler import daily_retry_state, pending_catchup_date, run_at_for_report_date, summary_window
from ..reminder_scheduler import reminder_loop
from .history_inspection import history_inspection_loop
from .operations import OperationBusy
from .worker_threads import guarded_to_thread


async def daily_scheduler_loop(
    config, archive, operations, feature_service, scheduler_state, run_daily_task,
):
    """Check every 60s if it's time to run the daily task."""
    import logging
    scheduler_logger = logging.getLogger("qq_digest.scheduler")
    while True:
        await asyncio.sleep(60)
        try:
            if (
                not config
                or setup_in_progress(config)
                or not feature_service.enabled('auto_daily')
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
                            catchup_result = await guarded_to_thread(
                                run_daily_task, config,
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
                        result = await guarded_to_thread(run_daily_task, config)
                        scheduler_state["last_result"] = result
                        if result.get("success"):
                            scheduler_state["last_run_date"] = today
                    finally:
                        scheduler_state["running"] = False
            except OperationBusy:
                continue
        except Exception as exc:
            scheduler_state["last_result"] = {
                "success": False, "status": "failed", "error": str(exc),
            }
            scheduler_logger.exception("日报调度失败，将在下一周期检查")


async def notification_queue_loop(config, operations, notifier):
    import logging
    from .storage_handoff import run_notification_queue

    while True:
        try:
            await run_notification_queue(config, operations, notifier)
        except Exception:
            logging.getLogger("qq_digest.notify").exception(
                "通知队列处理失败，将在下一周期重试"
            )
        await asyncio.sleep(60)


async def message_sync_loop(config, operations, feature_service, sync_scheduler_state):
    import logging
    sync_logger = logging.getLogger("qq_digest.sync")
    if not config:
        return
    await asyncio.sleep(config.collection.startup_delay_seconds)
    while True:
        try:
            if (
                config.collection.enabled
                and not setup_in_progress(config)
                and feature_service.enabled('auto_collection')
                and config.ntqq.enabled
                and operations.can_start("sync")
            ):
                from ..sync import run_message_sync

                try:
                    with operations.claim("sync"):
                        sync_scheduler_state["running"] = True
                        try:
                            result = await guarded_to_thread(
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
        except Exception as exc:
            sync_scheduler_state["last_result"] = {
                "success": False, "errors": [str(exc)],
            }
            sync_logger.exception("自动采集失败，将在下一周期重试")
        await asyncio.sleep(config.collection.interval_minutes * 60)


def background_lifespan(
    *, config, archive, candidates, knowledge, operations,
    scheduler_state, sync_scheduler_state, run_daily_task,
):
    """Bind existing runtime services and state to the application lifespan."""
    ws_state = {"listener": None, "task": None}
    notification_state = {"notifier": None, "task": None}

    @asynccontextmanager
    async def lifespan(app):
        feature_service = app.state.features
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
            ws_state["listener"] = WebSocketListener(notifier=notifier, handler=handler, operations=operations)
            ws_state["task"] = asyncio.get_event_loop().create_task(ws_state["listener"].start())
            notification_state["notifier"] = notifier
            notification_state["task"] = asyncio.get_event_loop().create_task(
                notification_queue_loop(config, operations, notifier)
            )

        # Start daily scheduler
        scheduler_state["task"] = asyncio.get_event_loop().create_task(daily_scheduler_loop(
            config, archive, operations, feature_service, scheduler_state, run_daily_task,
        ))
        sync_scheduler_state["task"] = asyncio.get_event_loop().create_task(
            message_sync_loop(config, operations, feature_service, sync_scheduler_state)
        )
        inspection_task = asyncio.create_task(history_inspection_loop(
            config, operations, enabled=lambda: feature_service.enabled('history_inspection') and not setup_in_progress(config)))
        reminder_task = asyncio.create_task(reminder_loop(
            archive.connection.execute('PRAGMA database_list').fetchone()['file'], operations,
            enabled=lambda: feature_service.enabled('reminders'),
            timezone_name=config.summary.timezone if config else 'Asia/Shanghai',
            sink_getter=lambda: getattr(app.state, 'reminder_sink', None)))

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
                inspection_task,
                reminder_task,
            )
            if task is not None
        ]
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await guarded_to_thread(app.state.close_topic_tracking)

    return lifespan
