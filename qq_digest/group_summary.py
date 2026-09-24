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
        quality_note = (
            f"原始 {summary.source_messages} 条，纳入 {summary.included_messages} 条，"
            f"清洗丢弃 {summary.discarded_messages} 条。"
            + (
                f"上下文达到上限，仅保留最近 {summary.context_chars} 字符。"
                if summary.context_truncated
                else ""
            )
        )
        markdown = render_markdown(
            group_name=group.name,
            report_date=report_date,
            window=f"{window_start.isoformat()} 到 {window_end.isoformat()}",
            overview=summary.response.overview,
            topics=[item.model_dump() for item in summary.response.main_topics],
            conclusions=summary.response.conclusions,
            resources=[item.model_dump() for item in summary.response.resources],
            tasks=[
                f"{item.owner}：{item.description}"
                + (f"（{item.deadline}）" if item.deadline else "")
                for item in summary.response.tasks
            ],
            open_questions=summary.response.open_questions,
            deterministic=summary.deterministic,
            quality_note=quality_note,
            title_suffix="范围摘要" if report_kind == "range" else "日报",
            date_label="日期范围" if report_kind == "range" else "日期",
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
            payload=summary.response.model_dump(),
            candidate_kwargs=candidate_kwargs,
            source_message_count=len(messages),
        )


def summary_input_fingerprint(
    *, group: GroupConfig, report_kind: str,
    messages: list[NormalizedMessage], timezone: ZoneInfo,
    knowledge_base: str, max_context_chars: int,
) -> str:
    """Hash every input that can materially change generated report content."""
    value = {
        "version": 3,
        "group": group.model_dump(mode="json", exclude={"template"}),
        "report_kind": report_kind,
        "timezone": timezone.key,
        "max_context_chars": max_context_chars,
        "knowledge_base": knowledge_base,
        "system_prompt": build_system_prompt(group.category),
        "messages": [message.model_dump(mode="json") for message in messages],
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
