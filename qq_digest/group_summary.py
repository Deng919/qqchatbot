from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import GroupConfig, NormalizedMessage
from .reports import render_markdown
from .prompt_builder import build_system_prompt
from .summary import Summarizer
from .report_completeness import input_coverage


# Bump when report rendering changes without a prompt/schema change.
REPORT_FORMAT_VERSION = 8


@dataclass(frozen=True)
class GroupSummaryArtifact:
    markdown: str
    payload: dict
    candidate_kwargs: tuple[dict[str, object], ...]
    source_message_count: int


class GroupSummaryBuilder:
    def __init__(self, summarizer: Summarizer):
        self.summarizer = summarizer

    def build(
        self,
        *,
        group: GroupConfig,
        window_start: datetime,
        window_end: datetime,
        report_date: str,
        candidate_date: str,
        messages: list[NormalizedMessage],
        timezone: ZoneInfo,
        knowledge_base: str,
        report_kind: str,
        archive_mismatch: bool = False,
    ) -> GroupSummaryArtifact:
        if report_kind not in {"daily", "range"}:
            raise ValueError("report_kind 只支持 daily 或 range")
        summary = self.summarizer.summarize(
            group_id=group.group_id,
            group_name=group.name,
            category=group.category,
            keywords=group.keywords,
            window_start=window_start,
            window_end=window_end,
            messages=messages,
            timezone=timezone,
            knowledge_base=knowledge_base,
        )
        diagnostics = {
            "source_messages": summary.source_messages,
            "included_messages": summary.included_messages,
            "discarded_messages": summary.discarded_messages,
            "context_truncated": summary.context_truncated,
            "context_chars": summary.context_chars,
            "extracted": summary.deterministic,
        }
        coverage = input_coverage(diagnostics, archive_mismatch=archive_mismatch)
        quality_note = "\n\n".join(coverage["notes"])
        markdown = render_markdown(
            group_name=group.name,
            report_date=report_date,
            window=(
                f"{window_start.astimezone(timezone):%Y-%m-%d %H:%M} 至 "
                f"{window_end.astimezone(timezone):%Y-%m-%d %H:%M}"
            ),
            overview=summary.response.overview,
            overview_source_ids=summary.response.overview_message_ids,
            topics=[item.model_dump() for item in summary.response.main_topics],
            conclusions=[
                item.model_dump() if hasattr(item, "model_dump")
                else {"text": item, "message_ids": []}
                for item in summary.response.conclusions
            ],
            resources=[item.model_dump() for item in summary.response.resources],
            tasks=[
                {
                    "text": f"{item.owner}：{item.description}"
                    + (f"（{item.deadline}）" if item.deadline else ""),
                    "message_ids": item.message_ids,
                }
                for item in summary.response.tasks
            ],
            open_questions=[
                item.model_dump() if hasattr(item, "model_dump")
                else {"text": item, "message_ids": []}
                for item in summary.response.open_questions
            ],
            deterministic=summary.deterministic,
            quality_note=quality_note,
            title_suffix="摘要",
            date_label="日期范围",
        )
        candidate_kwargs = (
            tuple(
                {
                    "group_id": group.group_id,
                    "created_date": candidate_date,
                    "candidate_type": candidate.type,
                    "title": candidate.title,
                    "link": candidate.link,
                    "content": candidate.content,
                    "reason": candidate.reason,
                    "excerpt": next(
                        (
                            message.text[:100]
                            for message in messages
                            if message.msg_id in candidate.message_ids
                        ),
                        "",
                    ),
                    "message_ids": candidate.message_ids,
                }
                for candidate in summary.response.candidates
            )
            if group.important_candidates
            else ()
        )
        return GroupSummaryArtifact(
            markdown=markdown,
            payload={**summary.response.model_dump(), "evidence_version": 1,
                     "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
                     "window_end_inclusive": report_kind == "daily",
                     "diagnostics": diagnostics, "coverage": coverage},
            candidate_kwargs=candidate_kwargs,
            source_message_count=len(messages),
        )


def summary_input_fingerprint(
    *, group: GroupConfig, report_kind: str,
    messages: list[NormalizedMessage], timezone: ZoneInfo,
    knowledge_base: str, max_context_chars: int,
    archive_mismatch: bool = False,
) -> str:
    """Hash every input that can materially change generated report content."""
    value = {
        "version": REPORT_FORMAT_VERSION,
        "group": group.model_dump(mode="json", exclude={"template"}),
        "report_kind": report_kind,
        "timezone": timezone.key,
        "max_context_chars": max_context_chars,
        "knowledge_base": knowledge_base,
        "system_prompt": build_system_prompt(group.category),
        "messages": [message.model_dump(mode="json") for message in messages],
    }
    if archive_mismatch:
        value["archive_mismatch"] = True
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
