"""Evidence selection for questions about one archived report."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .models import NormalizedMessage


class ReportNotFound(LookupError):
    pass


class NoReportEvidence(ValueError):
    pass


@dataclass(frozen=True)
class ReportEvidence:
    group_id: int
    start_date: str
    end_date: str
    markdown_path: Path
    messages: list[NormalizedMessage]


@dataclass(frozen=True)
class SelectedContext:
    text: str
    messages: list[NormalizedMessage]
    truncated: bool
    message_count: int


def load_report_evidence(
    archive: Archive, kind: str, report_id: int, timezone: ZoneInfo
) -> ReportEvidence:
    if kind == "daily":
        row = archive.connection.execute(
            "SELECT * FROM reports WHERE report_id=?", (report_id,)
        ).fetchone()
        if row is None:
            raise ReportNotFound("报告不存在")
        start_date = end_date = row["report_date"]
    elif kind == "range":
        row = archive.manual_report_by_id(report_id)
        if row is None:
            raise ReportNotFound("报告不存在")
        start_date, end_date = row["start_date"], row["end_date"]
    else:
        raise ReportNotFound("报告类型不存在")
    start = datetime.combine(date.fromisoformat(start_date), time.min, timezone)
    end = datetime.combine(date.fromisoformat(end_date) + timedelta(days=1), time.min, timezone)
    messages = archive.messages_in_window(int(row["group_id"]), start, end)
    if not messages:
        raise NoReportEvidence("该报告时段没有已归档的原始消息")
    return ReportEvidence(
        group_id=int(row["group_id"]),
        start_date=start_date,
        end_date=end_date,
        markdown_path=Path(row["markdown_path"]),
        messages=messages,
    )


def _format_message(message: NormalizedMessage) -> str:
    body = " ".join(message.text.split())
    return f"[{message.msg_id}|{message.timestamp:%m-%d %H:%M}|{message.sender_qq or '-'}] {body}"


def _terms(question: str) -> set[str]:
    normalized = question.lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def select_report_context(
    messages: list[NormalizedMessage], question: str, *, max_chars: int = 16000
) -> SelectedContext:
    if max_chars < 1:
        raise ValueError("上下文长度必须为正数")
    lines = [_format_message(item) for item in messages]
    whole = "\n".join(lines)
    if len(whole) <= max_chars:
        return SelectedContext(whole, list(messages), False, len(messages))
    terms = _terms(question)
    scored = []
    for index, item in enumerate(messages):
        body = item.text.lower()
        score = sum(3 if term.isascii() else 1 for term in terms if term in body)
        scored.append((score, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    has_match = bool(scored and scored[0][0] > 0)
    chosen: set[int] = set()
    used = 0
    for score, center in scored:
        if has_match and score == 0:
            break
        for index in (center, center - 1, center + 1):
            if index < 0 or index >= len(messages) or index in chosen:
                continue
            needed = len(lines[index]) + (1 if chosen else 0)
            if used + needed <= max_chars:
                chosen.add(index)
                used += needed
    indices = sorted(chosen)
    selected = [messages[index] for index in indices]
    text = "\n".join(lines[index] for index in indices)
    return SelectedContext(text, selected, True, len(selected))
