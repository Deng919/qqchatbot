from __future__ import annotations
import json
import logging
import time

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .candidates import CandidateService
from .collector.base import Collector
from .group_summary import GroupSummaryBuilder, summary_input_fingerprint
from .reports import ReportPaths, ReportWriter, published_report_is_valid
from .scheduler import summary_window
from .summary import Summarizer
from .notify import QQBotNotifier, BotConfig, NotificationQueueProcessor



logger = logging.getLogger(__name__)


def with_retry(fn, max_retries=3, base_delay=2.0, exceptions=(Exception,)):
    """指数退避重试。"""
    for attempt in range(max_retries):
        try:
            return fn()
        except exceptions as exc:
            if attempt + 1 >= max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("第 %d 次重试，%s 秒后重试: %s", attempt + 1, delay, exc)
            time.sleep(delay)


@dataclass
class DailyRunResult:
    report_date: str
    groups_processed: int
    messages_inserted: int
    report_paths: list[ReportPaths] = field(default_factory=list)
    candidate_ids: list[int] = field(default_factory=list)
    succeeded_groups: list[int] = field(default_factory=list)
    skipped_groups: list[int] = field(default_factory=list)
    failed_groups: list[GroupRunFailure] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "partial_success" if self.failed_groups else "success"


@dataclass(frozen=True)
class GroupRunFailure:
    group_id: int
    group_name: str
    stage: str
    error: str


def _group_failure(group, stage: str, exc: Exception) -> GroupRunFailure:
    detail = " ".join(str(exc).split())[:200]
    return GroupRunFailure(
        group_id=group.group_id,
        group_name=group.name,
        stage=stage,
        error=f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__,
    )


class DailyPipeline:
    def __init__(
        self,
        *,
        archive: Archive,
        collector: Collector,
        ai_client,
        report_dir: Path,
        max_context_chars: int,
        timezone_name: str,
        window_mode: str = "today",
        bot_config: BotConfig | None = None,
        knowledge_paths: dict[str, Path] | None = None,
    ) -> None:
        self.archive = archive
        self.collector = collector
        self.candidates = CandidateService(archive)
        self.report_dir = report_dir
        self.report_writer = ReportWriter(report_dir)
        self.max_context_chars = max_context_chars
        self.notifier = QQBotNotifier(bot_config) if bot_config else None
        self.summarizer = Summarizer(
            ai=ai_client,
            max_context_chars=max_context_chars,
        )
        self.timezone = ZoneInfo(timezone_name)
        self.window_mode = window_mode
        self.knowledge_paths = knowledge_paths or {
            "resource": self.report_dir.parent / "knowledge" / "resources.md",
            "experience": self.report_dir.parent / "knowledge" / "experiences.md",
        }

    def _load_knowledge_base(self) -> str:
        """读取知识库文件内容用于 AI 去重。"""
        parts = []
        for path in self.knowledge_paths.values():
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if content.strip():
                    parts.append(content)
        return "\n\n".join(parts)

    def _send_notifications(
        self,
        report_id: int,
        report_date: str,
        candidate_count: int,
        group_name: str,
    ) -> None:
        """Persist QQ Bot notifications and attempt currently due items."""
        if not self.notifier or not self.notifier.config.app_id:
            return
        for openid in self.notifier.config.allowed_openids:
            self.archive.enqueue_notification(
                report_id=report_id,
                channel="qq_bot_private",
                recipient=openid,
                payload={
                    "group_name": group_name,
                    "report_date": report_date,
                    "pending_candidates": candidate_count,
                },
            )
        NotificationQueueProcessor(
            archive=self.archive,
            notifier=self.notifier,
            max_attempts=self.notifier.config.max_retries,
            retry_base_seconds=self.notifier.config.retry_base_seconds,
        ).process_due()

    def run_daily(self, now: datetime) -> DailyRunResult:
        job_id = self.archive.start_job("daily_digest")
        try:
            result = self._run_without_job_tracking(now)
        except Exception as exc:
            self.archive.finish_job(job_id, "failed", str(exc))
            raise
        self.archive.finish_job(
            job_id,
            result.status,
            json.dumps(
                [asdict(item) for item in result.failed_groups], ensure_ascii=False
            )
            if result.failed_groups
            else "",
        )
        return result

    def _run_without_job_tracking(self, now: datetime) -> DailyRunResult:
        local_now = now.astimezone(self.timezone)
        start, end = summary_window(now, self.window_mode, self.timezone)
        if self.window_mode == "today":
            report_date = local_now.date().isoformat()
        else:
            report_date = start.date().isoformat()
        result = DailyRunResult(
            report_date=report_date,
            groups_processed=0,
            messages_inserted=0,
        )

        prepared_groups = []
        knowledge_base = self._load_knowledge_base()
        for group in self.archive.enabled_groups(daily_summary=True):
            try:
                raw_msgs = list(self.collector.collect(group.group_id, start, end))
            except Exception as exc:
                logger.error("采集群 %s 失败: %s", group.name, exc)
                self.archive.mark_sync_failure(
                    group_id=group.group_id,
                    status="adapter_incompatible",
                )
                result.failed_groups.append(_group_failure(group, "collection", exc))
                continue
            ingest = self.archive.ingest(raw_msgs)
            result.messages_inserted += ingest.inserted
            self.archive.mark_sync(group_id=group.group_id, last_timestamp=end)

            messages = self.archive.messages_between(group.group_id, start, end)
            if not messages:
                result.groups_processed += 1
                result.skipped_groups.append(group.group_id)
                continue
            input_fingerprint = summary_input_fingerprint(
                group=group,
                report_kind="daily",
                messages=messages,
                timezone=self.timezone,
                knowledge_base=knowledge_base,
                max_context_chars=self.max_context_chars,
            )
            existing_report = self.archive.report_for(group.group_id, report_date)
            if (
                existing_report is not None
                and existing_report["input_fingerprint"] == input_fingerprint
                and published_report_is_valid(
                    existing_report["markdown_path"], existing_report["json_path"]
                )
            ):
                result.candidate_ids.extend(
                    self.archive.candidates_for_report(existing_report["candidate_ids"])
                )
                result.groups_processed += 1
                result.skipped_groups.append(group.group_id)
                continue

            prepared_groups.append(
                (group, raw_msgs, messages, input_fingerprint, existing_report)
            )

        # Collection is deliberately completed for every group before the
        # first AI request. Provider outages must not make today's source
        # messages disappear from the archive and collection UI.
        for group, raw_msgs, messages, input_fingerprint, existing_report in prepared_groups:
            try:
                if len(messages) < len(raw_msgs) // 2:
                    logger.warning(
                        "群 %s 归档消息数异常低: 采集 %d, 归档 %d",
                        group.name,
                        len(raw_msgs),
                        len(messages),
                    )
                artifact = GroupSummaryBuilder(self.summarizer).build(
                    group=group,
                    window_start=start,
                    window_end=end,
                    report_date=report_date,
                    candidate_date=report_date,
                    messages=messages,
                    timezone=self.timezone,
                    knowledge_base=knowledge_base,
                    report_kind="daily",
                )
                prepared = self.report_writer.prepare_named(
                    f"{report_date}__{group.group_id}",
                    artifact.markdown,
                    artifact.payload,
                )
                old_candidate_ids = (
                    self.archive.candidates_for_report(
                        existing_report["candidate_ids"]
                    )
                    if existing_report is not None
                    else []
                )
                try:
                    with self.archive.transaction():
                        group_candidate_ids = [
                            self.candidates.create_in_transaction(**kwargs)
                            for kwargs in artifact.candidate_kwargs
                        ]
                        report_id = self.archive.record_report_in_transaction(
                            group_id=group.group_id,
                            report_date=report_date,
                            effective_template="adaptive",
                            markdown_path=prepared.paths.markdown,
                            json_path=prepared.paths.json,
                            candidate_ids=group_candidate_ids,
                            input_fingerprint=input_fingerprint,
                            source_message_count=artifact.source_message_count,
                        )
                        self.archive.delete_unreferenced_pending_candidates_in_transaction(
                            old_candidate_ids
                        )
                        prepared.install()
                except Exception:
                    prepared.rollback()
                    raise
                prepared.finalize()
                self._send_notifications(
                    report_id,
                    report_date,
                    len(group_candidate_ids),
                    group.name,
                )
                result.report_paths.append(prepared.paths)
                result.candidate_ids.extend(group_candidate_ids)
                result.groups_processed += 1
                result.succeeded_groups.append(group.group_id)
            except Exception as exc:
                logger.error(
                    "群 %s 摘要与报告处理失败: %s",
                    group.name,
                    exc,
                    exc_info=True,
                )
                result.failed_groups.append(_group_failure(group, "summary", exc))

        return result
