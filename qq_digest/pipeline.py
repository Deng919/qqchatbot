from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .candidates import CandidateService
from .collector.base import Collector
from .reports import ReportPaths, ReportWriter, render_markdown
from .scheduler import summary_window
from .summary import Summarizer


@dataclass
class DailyRunResult:
    report_date: str
    groups_processed: int
    messages_inserted: int
    report_paths: list[ReportPaths] = field(default_factory=list)
    candidate_ids: list[int] = field(default_factory=list)


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
    ) -> None:
        self.archive = archive
        self.collector = collector
        self.candidates = CandidateService(archive)
        self.report_writer = ReportWriter(report_dir)
        self.summarizer = Summarizer(
            ai=ai_client,
            max_context_chars=max_context_chars,
        )
        self.timezone = ZoneInfo(timezone_name)
        self.window_mode = window_mode

    def run_daily(self, now: datetime) -> DailyRunResult:
        job_id = self.archive.start_job("daily_digest")
        try:
            result = self._run_without_job_tracking(now)
        except Exception as exc:
            self.archive.finish_job(job_id, "failed", str(exc))
            raise
        self.archive.finish_job(job_id, "success")
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

        for group in self.archive.enabled_groups(daily_summary=True):
            ingest = self.archive.ingest(
                self.collector.collect(group.group_id, start, end)
            )
            result.messages_inserted += ingest.inserted
            self.archive.mark_sync(group_id=group.group_id, last_timestamp=end)

            existing_report = self.archive.report_for(group.group_id, report_date)
            if existing_report is not None:
                result.candidate_ids.extend(
                    self.archive.candidates_for_report(existing_report["candidate_ids"])
                )
                result.groups_processed += 1
                continue

            messages = self.archive.messages_between(group.group_id, start, end)
            summary = self.summarizer.summarize(
                group_id=group.group_id,
                group_name=group.name,
                window_start=start,
                window_end=end,
                messages=messages,
                timezone=self.timezone,
            )
            display_start = start.isoformat()
            display_end = end.isoformat()
            markdown = render_markdown(
                group_name=group.name,
                report_date=report_date,
                window=f"{display_start} 到 {display_end}",
                topics=[item.topic for item in summary.response.main_topics],
                conclusions=summary.response.conclusions,
                resources=[
                    item.title if not item.link else f"[{item.title}]({item.link})"
                    for item in summary.response.resources
                ],
                tasks=summary.response.tasks,
                open_questions=summary.response.open_questions,
                deterministic=summary.deterministic,
                quality_note=(
                    f"上下文截断后保留 {summary.context_chars} 字符，已按完整行保留最近消息。"
                    if summary.context_chars >= self.summarizer.max_context_chars
                    else ""
                ),
            )
            paths = self.report_writer.write(
                group_id=group.group_id,
                group_name=group.name,
                report_date=report_date,
                markdown=markdown,
                payload=summary.response.model_dump(),
            )
            result.report_paths.append(paths)

            group_candidate_ids: list[int] = []
            if group.important_candidates:
                for candidate in summary.response.candidates:
                    candidate_id = self.candidates.create(
                        group_id=group.group_id,
                        created_date=report_date,
                        candidate_type=candidate.type,
                        title=candidate.title,
                        link=candidate.link,
                        content=candidate.content,
                        reason=candidate.reason,
                        excerpt=next(
                            (
                                message.text[:100]
                                for message in messages
                                if message.msg_id in candidate.message_ids
                            ),
                            "",
                        ),
                        message_ids=candidate.message_ids,
                    )
                    result.candidate_ids.append(candidate_id)
                    group_candidate_ids.append(candidate_id)
            self.archive.record_report(
                group_id=group.group_id,
                report_date=report_date,
                markdown_path=paths.markdown,
                json_path=paths.json,
                candidate_ids=group_candidate_ids,
            )
            result.groups_processed += 1

        return result
