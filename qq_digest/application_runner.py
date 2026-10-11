"""Application-level composition for the daily digest runtime."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .ai import factory as ai_factory
from .archive import Archive
from .collector.base import Collector
from .collector.fixture import FixtureCollector
from .collector.ntqq import NTQQCollector
from .config import Config
from .notify import BotConfig
from .pipeline import DailyPipeline
from . import refresh as database_refresh
from .scheduler import summary_window


logger = logging.getLogger("qq_digest.scheduler")


def build_collector(config: Config) -> Collector:
    """Build the collector selected by the configured message source."""
    if config.ntqq.enabled and config.ntqq.db_dir:
        return NTQQCollector(
            db_dir=config.ntqq.db_dir,
            qq_number=config.ntqq.qq_number,
            timezone_name=config.ntqq.timezone,
        )
    return FixtureCollector()


def build_bot_config(config: Config) -> BotConfig | None:
    """Translate configured QQ Bot settings into the pipeline value object."""
    if not config.qq_bot.enabled or not config.qq_bot.app_id:
        return None
    return BotConfig(
        app_id=config.qq_bot.app_id,
        app_secret=os.environ.get(config.qq_bot.app_secret_env, ""),
        allowed_openids=config.qq_bot.allowed_openids,
        api_base=config.qq_bot.api_base,
        websocket_url=config.qq_bot.websocket_url,
        max_retries=config.qq_bot.max_retries,
        retry_base_seconds=config.qq_bot.retry_base_seconds,
    )


def _record_preflight_failure(
    config: Config,
    *,
    target_date: str,
    retry_of_job_id: int | None,
    error: str,
) -> None:
    failure_archive = Archive.open(config.archive_path)
    try:
        job_id = failure_archive.start_job(
            "daily_digest",
            target_date=target_date,
            retry_of_job_id=retry_of_job_id,
        )
        failure_archive.finish_job(job_id, "failed", error)
    finally:
        failure_archive.close()


def run_daily_task(
    config: Config,
    *,
    scheduled_at: datetime | None = None,
    retry_of_job_id: int | None = None,
    refresh: bool = True,
) -> dict:
    """Run the daily digest runtime and return the Web-compatible result."""
    worker_archive = None
    ai_client = None
    pipeline_started = False
    scheduled_at = scheduled_at or datetime.now(ZoneInfo(config.summary.timezone))
    target_date = summary_window(
        scheduled_at,
        config.summary.window_mode,
        ZoneInfo(config.summary.timezone),
    )[0].date().isoformat()

    try:
        ai_client = ai_factory.build_ai_client(config)

        if refresh and config.ntqq.enabled and config.ntqq.db_dir:
            logger.info("定时任务: 刷新 NTQQ 数据库...")
            refresh_result = database_refresh.refresh_database(
                qq_number=config.ntqq.qq_number,
                output_dir=config.ntqq.db_dir,
                snapshot_root=config.work_dir / "snapshots",
                cache_owner=config.data_dir,
            )
            logger.info("定时任务刷新: %s", refresh_result.message)
            if not refresh_result.success:
                error = f"数据库刷新失败: {refresh_result.message}"
                _record_preflight_failure(
                    config,
                    target_date=target_date,
                    retry_of_job_id=retry_of_job_id,
                    error=error,
                )
                return {"success": False, "status": "failed", "error": error}

        worker_archive = Archive.open(config.archive_path)
        pipeline = DailyPipeline(
            archive=worker_archive,
            attachment_config=config,
            collector=build_collector(config),
            ai_client=ai_client,
            report_dir=config.report_dir,
            max_context_chars=config.ai.max_context_chars,
            timezone_name=config.summary.timezone,
            window_mode=config.summary.window_mode,
            knowledge_paths=config.resolve_knowledge_paths(),
            bot_config=build_bot_config(config),
        )
        pipeline_started = True
        result = pipeline.run_daily(
            scheduled_at,
            retry_of_job_id=retry_of_job_id,
        )
        logger.info(
            "定时任务完成: %d 群, %d 消息, %d 报告, %d 候选",
            result.groups_processed,
            result.messages_inserted,
            len(result.report_paths),
            len(result.candidate_ids),
        )
        return {
            "success": result.status == "success",
            "status": result.status,
            "report_date": target_date,
            "groups_processed": result.groups_processed,
            "messages_inserted": result.messages_inserted,
            "reports": len(result.report_paths),
            "candidates": len(result.candidate_ids),
            "succeeded_groups": result.succeeded_groups,
            "skipped_groups": result.skipped_groups,
            "failed_groups": [asdict(item) for item in result.failed_groups],
        }
    except Exception as exc:
        logger.error("定时任务失败: %s", exc, exc_info=True)
        if not pipeline_started:
            _record_preflight_failure(
                config,
                target_date=target_date,
                retry_of_job_id=retry_of_job_id,
                error=str(exc),
            )
        return {"success": False, "status": "failed", "error": str(exc)}
    finally:
        if ai_client is not None:
            ai_client.close()
        if worker_archive is not None:
            worker_archive.close()
