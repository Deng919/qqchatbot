from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .archive import Archive
from .candidates import CandidateService
from .group_summary import GroupSummaryBuilder, summary_input_fingerprint
from .reports import ReportWriter, published_report_is_valid
from .summary import Summarizer


logger = logging.getLogger(__name__)
@dataclass(frozen=True)
class ManualSummaryRequest:
    group_ids: Sequence[int]
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        group_ids = tuple(self.group_ids)
        object.__setattr__(self, "group_ids", group_ids)
        if not group_ids:
            raise ValueError("至少选择一个群")
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("群列表中存在重复项")
        if self.start_date > self.end_date:
            raise ValueError("开始日期不得晚于结束日期")
        if (self.end_date - self.start_date).days + 1 > 7:
            raise ValueError("最多选择 7 个自然日")


@dataclass(frozen=True)
class ManualReportResult:
    report_id: int
    report_key: str
    group_id: int
    group_name: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class ManualGroupFailure:
    group_id: int
    group_name: str
    stage: str
    error: str


@dataclass
class ManualSummaryResult:
    created_reports: list[ManualReportResult] = field(default_factory=list)
    reused_reports: list[ManualReportResult] = field(default_factory=list)
    skipped_groups: list[dict[str, object]] = field(default_factory=list)
    failed_groups: list[ManualGroupFailure] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "partial_success" if self.failed_groups else "success"


def range_window(
    request: ManualSummaryRequest, timezone: ZoneInfo
) -> tuple[datetime, datetime]:
    start = datetime.combine(request.start_date, time.min, tzinfo=timezone)
    end = datetime.combine(
        request.end_date + timedelta(days=1), time.min, tzinfo=timezone
    )
    return start, end


class ManualSummaryService:
    def __init__(
        self,
        *,
        archive: Archive,
        ai_client,
        report_dir: Path,
        max_context_chars: int,
        timezone_name: str,
        knowledge_paths: dict[str, Path] | None = None,
    ) -> None:
        self.archive = archive
        self.candidates = CandidateService(archive)
        self.report_writer = ReportWriter(report_dir)
        self.max_context_chars = max_context_chars
        self.summarizer = Summarizer(
            ai=ai_client,
            max_context_chars=max_context_chars,
        )
        self.timezone = ZoneInfo(timezone_name)
        self.knowledge_paths = knowledge_paths or {}

    def run(self, request: ManualSummaryRequest) -> ManualSummaryResult:
        job_id = self.archive.start_job("manual_summary")
        try:
            groups = self._selected_groups(request.group_ids)
            result = self._run_groups(request, groups)
        except Exception as exc:
            self.archive.finish_job(job_id, "failed", self._safe_error(exc))
            raise
        self.archive.finish_job(
            job_id,
            result.status,
            (
                json.dumps(
                    [asdict(item) for item in result.failed_groups],
                    ensure_ascii=False,
                )
                if result.failed_groups
                else ""
            ),
        )
        return result

    def _selected_groups(self, group_ids: Sequence[int]):
        enabled = {group.group_id: group for group in self.archive.enabled_groups()}
        missing = [group_id for group_id in group_ids if group_id not in enabled]
        if missing:
            joined = "、".join(str(group_id) for group_id in missing)
            raise ValueError(f"群 {joined} 不存在或未启用")
        return [enabled[group_id] for group_id in group_ids]

    def _run_groups(self, request, groups) -> ManualSummaryResult:
        start, end = range_window(request, self.timezone)
        start_date = request.start_date.isoformat()
        end_date = request.end_date.isoformat()
        report_date = f"{start_date} 至 {end_date}"
        result = ManualSummaryResult()
        builder = GroupSummaryBuilder(self.summarizer)
        knowledge_base = self._load_knowledge_base()

        for group in groups:
            messages = self.archive.messages_in_window(group.group_id, start, end)
            if not messages:
                result.skipped_groups.append(
                    {
                        "group_id": group.group_id,
                        "group_name": group.name,
                        "reason": "所选范围没有归档消息",
                    }
                )
                continue
            fingerprint = summary_input_fingerprint(
                group=group,
                report_kind="range",
                messages=messages,
                timezone=self.timezone,
                knowledge_base=knowledge_base,
                max_context_chars=self.max_context_chars,
            )
            existing = self.archive.manual_report_for(
                group.group_id,
                start_date,
                end_date,
                "adaptive",
            )
            if (
                existing is not None
                and existing["input_fingerprint"] == fingerprint
                and published_report_is_valid(
                    existing["markdown_path"], existing["json_path"]
                )
            ):
                result.reused_reports.append(self._report_result(existing, group))
                continue

            stage = "summary"
            try:
                artifact = builder.build(
                    group=group,
                    window_start=start,
                    window_end=end,
                    report_date=report_date,
                    candidate_date=end_date,
                    messages=messages,
                    timezone=self.timezone,
                    knowledge_base=knowledge_base,
                    report_kind="range",
                )
                stage = "publication"
                stem = (
                    f"range__{start_date}__{end_date}__"
                    f"{group.group_id}__adaptive"
                )
                payload = {
                    **artifact.payload,
                    "report_kind": "range",
                    "start_date": start_date,
                    "end_date": end_date,
                }
                prepared = self.report_writer.prepare_named(
                    stem, artifact.markdown, payload
                )
                old_candidate_ids = (
                    self.archive.candidates_for_report(existing["candidate_ids"])
                    if existing is not None
                    else []
                )
                try:
                    with self.archive.transaction():
                        candidate_ids = [
                            self.candidates.create_in_transaction(**kwargs)
                            for kwargs in artifact.candidate_kwargs
                        ]
                        report_id = self.archive.record_manual_report_in_transaction(
                            group_id=group.group_id,
                            start_date=start_date,
                            end_date=end_date,
                            detail_mode="adaptive",
                            effective_template="adaptive",
                            markdown_path=prepared.paths.markdown,
                            json_path=prepared.paths.json,
                            candidate_ids=candidate_ids,
                            input_fingerprint=fingerprint,
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
                row = self.archive.manual_report_by_id(report_id)
                if row is None:
                    raise RuntimeError("范围报告记录不存在")
                result.created_reports.append(self._report_result(row, group))
            except Exception as exc:
                logger.error(
                    "群 %s 手动范围总结失败: %s",
                    group.name,
                    exc,
                    exc_info=True,
                )
                result.failed_groups.append(
                    ManualGroupFailure(
                        group_id=group.group_id,
                        group_name=group.name,
                        stage=stage,
                        error=self._safe_error(exc),
                    )
                )
        return result

    def _load_knowledge_base(self) -> str:
        parts: list[str] = []
        for path in self.knowledge_paths.values():
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if content.strip():
                    parts.append(content)
        return "\n\n".join(parts)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        detail = " ".join(str(exc).split())[:200]
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

    @staticmethod
    def _report_result(row, group) -> ManualReportResult:
        report_id = int(row["manual_report_id"])
        return ManualReportResult(
            report_id=report_id,
            report_key=f"range:{report_id}",
            group_id=group.group_id,
            group_name=group.name,
            start_date=row["start_date"],
            end_date=row["end_date"],
        )
