from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator

from .models import NormalizedMessage
from .preprocessing import Preprocessor


class Topic(BaseModel):
    topic: str
    summary: str


class Resource(BaseModel):
    title: str
    link: str = ""


class CandidateOutput(BaseModel):
    type: str
    title: str
    link: str = ""
    content: str = ""
    reason: str
    message_ids: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in {"resource", "experience"}:
            raise ValueError("candidate.type 只支持 resource 或 experience")
        return value


class SummaryResponse(BaseModel):
    group_id: int
    main_topics: list[Topic]
    conclusions: list[str]
    resources: list[Resource]
    tasks: list[str]
    open_questions: list[str]
    candidates: list[CandidateOutput]


@dataclass(frozen=True)
class SummaryResult:
    response: SummaryResponse
    deterministic: dict[str, list[str]]
    context_chars: int


def build_context(messages: list[NormalizedMessage], max_chars: int) -> str:
    lines = []
    for message in messages:
        sender = message.sender_qq or "unknown"
        timestamp = message.timestamp.strftime("%H:%M")
        text = message.text.strip() or message.raw_digest.strip() or message.message_type
        lines.append(f"[{timestamp}|{sender}] {text}")
    context = "\n".join(lines)
    if len(context) <= max_chars:
        return context
    selected: list[str] = []
    total = 0
    for line in reversed(context.splitlines()):
        line_length = len(line) + (1 if selected else 0)
        if total + line_length > max_chars:
            break
        selected.append(line)
        total += line_length
    return "\n".join(reversed(selected))


class Summarizer:
    # Prompt 内容以 docs/ai/prompt-template.md 为基准。
    # 各分类策略见 docs/ai/summary-guidelines.md，候选标准见 docs/ai/candidate-criteria.md。
    # 当前 MVP 硬编码 prompt，后续改为按 category + template 动态组装。
    prompt = """你是可靠的QQ群消息摘要助手。请只输出JSON，不输出Markdown代码块。
JSON结构：
{
  "group_id": int,
  "main_topics": [{"topic": string, "summary": string}],
  "conclusions": [string],
  "resources": [{"title": string, "link": string}],
  "tasks": [string],
  "open_questions": [string],
  "candidates": [{"type": "resource"|"experience", "title": string, "link": string, "content": string, "reason": string, "message_ids": [string]}]
}
要求：
- 基于输入消息，不编造。
- 候选必须高价值、可复用、值得长期保存。
- 不要把闲聊作为候选。
"""

    def __init__(self, *, ai, max_context_chars: int):
        self.ai = ai
        self.max_context_chars = max_context_chars
        self.preprocessor = Preprocessor()

    def summarize(
        self,
        *,
        group_id: int,
        group_name: str,
        window_start: datetime,
        window_end: datetime,
        messages: list[NormalizedMessage],
        timezone: ZoneInfo,
    ) -> SummaryResult:
        if any(message.timestamp.tzinfo is None for message in messages):
            raise ValueError("消息时间必须带时区")
        local_messages = [
            message.model_copy(update={"timestamp": message.timestamp.astimezone(timezone)})
            for message in messages
        ]
        context = build_context(local_messages, self.max_context_chars)
        cleaned = self.preprocessor.process([message.text for message in messages])
        deterministic = {
            "links": cleaned.links,
            "files": cleaned.files,
            "todos": cleaned.todos,
        }
        raw = self.ai.chat(
            [
                {"role": "system", "content": self.prompt},
                {
                    "role": "user",
                    "content": (
                        f"群ID: {group_id}\n群名: {group_name}\n"
                        f"时间窗: {window_start.isoformat()} 到 {window_end.isoformat()}\n"
                        f"确定性提取: {deterministic}\n\n消息：\n{context}"
                    ),
                },
            ]
        )
        try:
            response = SummaryResponse.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"AI响应结构无效: {exc}") from exc
        if response.group_id != group_id:
            raise ValueError("AI响应 group_id 不匹配")
        return SummaryResult(
            response=response,
            deterministic=deterministic,
            context_chars=len(context),
        )
