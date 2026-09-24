from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, Field, ValidationError, ValidationInfo, field_validator

from .models import NormalizedMessage
from .preprocessing import Preprocessor
from .prompt_builder import build_system_prompt, build_user_prompt, CANDIDATE_LIMITS


class Topic(BaseModel):
    topic: str
    summary: str


class Resource(BaseModel):
    title: str
    url: str = Field(default="", validation_alias=AliasChoices("url", "link"))
    description: str = ""


class TaskOutput(BaseModel):
    owner: str
    description: str
    deadline: str = ""


class CandidateOutput(BaseModel):
    type: str
    title: str
    link: str = ""
    content: str = ""
    reason: str
    message_ids: list[str] = Field(default_factory=list)

    @field_validator("message_ids", mode="before")
    @classmethod
    def normalize_integer_message_ids(cls, value):
        if not isinstance(value, list):
            return value
        return [
            str(message_id)
            if isinstance(message_id, int) and not isinstance(message_id, bool)
            else message_id
            for message_id in value
        ]

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in {"resource", "experience"}:
            raise ValueError("candidate.type 只支持 resource 或 experience")
        return value


class SummaryResponse(BaseModel):
    group_id: int
    overview: str = ""
    main_topics: list[Topic]
    conclusions: list[str]
    resources: list[Resource]
    tasks: list[TaskOutput]
    open_questions: list[str]
    candidates: list[CandidateOutput]

    @field_validator("conclusions", "open_questions", mode="before")
    @classmethod
    def normalize_known_wrapped_text(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if not isinstance(value, list):
            return value
        key = "description" if info.field_name == "conclusions" else "question"
        return [
            item[key]
            if isinstance(item, dict)
            and set(item) == {key}
            and isinstance(item[key], str)
            else item
            for item in value
        ]


@dataclass(frozen=True)
class SummaryResult:
    response: SummaryResponse
    deterministic: dict[str, list[str]]
    context_chars: int
    context_truncated: bool = False
    source_messages: int = 0
    included_messages: int = 0
    discarded_messages: int = 0
    included_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextWindow:
    text: str
    chars: int
    truncated: bool
    source_messages: int
    included_messages: int
    discarded_messages: int
    included_message_ids: tuple[str, ...] = ()


def build_context_window(
    messages: list[NormalizedMessage],
    max_chars: int,
    cleaned_text_by_index: dict[int, str] | None = None,
) -> ContextWindow:
    lines: list[tuple[str, str]] = []
    for index, message in enumerate(messages):
        if cleaned_text_by_index is not None and index not in cleaned_text_by_index:
            continue
        sender = message.sender_qq or "unknown"
        timestamp = message.timestamp.strftime("%Y-%m-%d %H:%M")
        if cleaned_text_by_index is None:
            text = message.text.strip() or message.raw_digest.strip() or message.message_type
        else:
            text = cleaned_text_by_index[index]
        lines.append((message.msg_id, f"[{message.msg_id}|{timestamp}|{sender}] {text}"))
    context = "\n".join(line for _, line in lines)
    if len(context) <= max_chars:
        return ContextWindow(
            text=context,
            chars=len(context),
            truncated=False,
            source_messages=len(messages),
            included_messages=len(lines),
            discarded_messages=len(messages) - len(lines),
            included_message_ids=tuple(message_id for message_id, _ in lines),
        )
    selected: list[tuple[str, str]] = []
    total = 0
    for item in reversed(lines):
        _, line = item
        line_length = len(line) + (1 if selected else 0)
        if len(line) > max_chars:
            continue
        if total + line_length > max_chars:
            break
        selected.append(item)
        total += line_length
    selected.reverse()
    text = "\n".join(line for _, line in selected)
    return ContextWindow(
        text=text,
        chars=len(text),
        truncated=True,
        source_messages=len(messages),
        included_messages=len(selected),
        discarded_messages=len(messages) - len(lines),
        included_message_ids=tuple(message_id for message_id, _ in selected),
    )


def build_context(messages: list[NormalizedMessage], max_chars: int) -> str:
    return build_context_window(messages, max_chars).text


class Summarizer:
    """按 docs/ai 规范动态组装 prompt 的摘要生成器。"""

    def __init__(self, *, ai, max_context_chars: int):
        self.ai = ai
        self.max_context_chars = max_context_chars
        self.preprocessor = Preprocessor()

    def summarize(
        self,
        *,
        group_id: int,
        group_name: str,
        category: str = "general",
        keywords: list[str] | None = None,
        window_start: datetime,
        window_end: datetime,
        messages: list[NormalizedMessage],
        timezone: ZoneInfo,
        knowledge_base: str = "",
    ) -> SummaryResult:
        if any(message.timestamp.tzinfo is None for message in messages):
            raise ValueError("消息时间必须带时区")
        local_messages = [
            message.model_copy(update={"timestamp": message.timestamp.astimezone(timezone)})
            for message in messages
        ]
        source_texts = [
            message.text.strip() or message.raw_digest.strip() or message.message_type
            for message in local_messages
        ]
        cleaned = self.preprocessor.process(source_texts)
        cleaned_text_by_index = dict(
            zip(cleaned.kept_indexes, cleaned.cleaned_lines, strict=True)
        )
        context_window = build_context_window(
            local_messages,
            self.max_context_chars,
            cleaned_text_by_index=cleaned_text_by_index,
        )
        if not context_window.text:
            raise ValueError("清洗或截断后没有可用于摘要的消息")
        deterministic = {
            "links": cleaned.links,
            "files": cleaned.files,
            "todos": cleaned.todos,
        }
        report_date = window_start.astimezone(timezone).date().isoformat()
        system_prompt = build_system_prompt(category)
        user_prompt = build_user_prompt(
            group_id=group_id,
            group_name=group_name,
            category=category,
            report_date=report_date,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            message_count=context_window.included_messages,
            source_message_count=len(messages),
            discarded_message_count=cleaned.discarded_count,
            context=context_window.text,
            deterministic=deterministic,
            keywords=keywords,
            knowledge_base=knowledge_base,
            context_truncated=context_window.truncated,
        )
        raw = self.ai.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        try:
            response = SummaryResponse.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"AI响应结构无效: {exc}") from exc
        if response.group_id != group_id:
            raise ValueError("AI响应 group_id 不匹配")
        limit = CANDIDATE_LIMITS.get(category, 1)
        included_ids = set(context_window.included_message_ids)
        valid_candidates = []
        for candidate in response.candidates[:limit]:
            message_ids = list(
                dict.fromkeys(
                    message_id
                    for message_id in candidate.message_ids
                    if message_id in included_ids
                )
            )
            if message_ids:
                valid_candidates.append(
                    candidate.model_copy(update={"message_ids": message_ids})
                )
        response = response.model_copy(update={"candidates": valid_candidates})
        return SummaryResult(
            response=response,
            deterministic=deterministic,
            context_chars=context_window.chars,
            context_truncated=context_window.truncated,
            source_messages=context_window.source_messages,
            included_messages=context_window.included_messages,
            discarded_messages=context_window.discarded_messages,
            included_message_ids=context_window.included_message_ids,
        )
