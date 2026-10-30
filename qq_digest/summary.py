from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, Field, ValidationError, ValidationInfo, field_validator

from .models import NormalizedMessage
from .preprocessing import Preprocessor
from .prompt_builder import build_system_prompt, build_user_prompt, CANDIDATE_LIMITS
from .ai.client import reject_response
from .sender_identity import sender_fields


def _normalize_message_ids(value):
    if value is None:
        return []
    if not isinstance(value, list):
        return value
    return [
        str(message_id)
        if isinstance(message_id, int) and not isinstance(message_id, bool)
        else message_id
        for message_id in value
    ]


def _normalize_text(value):
    return "" if value is None else value


class EvidenceItem(BaseModel):
    message_ids: list[str] = Field(default_factory=list)

    @field_validator("message_ids", mode="before")
    @classmethod
    def normalize_message_ids(cls, value):
        return _normalize_message_ids(value)


class Topic(EvidenceItem):
    topic: str
    summary: str


class Resource(EvidenceItem):
    title: str
    url: str = Field(default="", validation_alias=AliasChoices("url", "link"))
    description: str = ""

    _normalize_text_fields = field_validator("title", "url", "description", mode="before")(_normalize_text)


class TaskOutput(EvidenceItem):
    owner: str
    description: str
    deadline: str = ""

    _normalize_text_fields = field_validator("owner", "description", "deadline", mode="before")(_normalize_text)


class CandidateOutput(BaseModel):
    type: str
    title: str
    link: str = ""
    content: str = ""
    reason: str
    message_ids: list[str] = Field(default_factory=list)

    _normalize_text_fields = field_validator("title", "link", "content", "reason", mode="before")(_normalize_text)

    @field_validator("message_ids", mode="before")
    @classmethod
    def normalize_integer_message_ids(cls, value):
        return _normalize_message_ids(value)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in {"resource", "experience"}:
            raise ValueError("candidate.type 只支持 resource 或 experience")
        return value


class CitedText(EvidenceItem):
    text: str


class SummaryResponse(BaseModel):
    group_id: int
    overview: str = ""
    overview_message_ids: list[str] = Field(default_factory=list)
    main_topics: list[Topic]
    conclusions: list[str | CitedText]
    resources: list[Resource]
    tasks: list[TaskOutput]
    open_questions: list[str | CitedText]
    candidates: list[CandidateOutput]

    @field_validator("overview_message_ids", mode="before")
    @classmethod
    def normalize_overview_message_ids(cls, value):
        return _normalize_message_ids(value)

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
        sender = sender_fields({'sender_qq': message.sender_qq, 'content_json': message.content_json}).get('sender_label') or message.sender_qq or "unknown"
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


def _complete_contexts(messages, cleaned_text_by_index, max_chars):
    """Pack every cleaned source in order; repeat its identity when splitting it."""
    chunks = []
    lines = []
    ids = []
    size = 0
    for index, message in enumerate(messages):
        if index not in cleaned_text_by_index:
            continue
        sender = sender_fields({
            "sender_qq": message.sender_qq, "content_json": message.content_json,
        }).get("sender_label") or message.sender_qq or "unknown"
        prefix = f"[{message.msg_id}|{message.timestamp:%Y-%m-%d %H:%M}|{sender}] "
        available = max_chars - len(prefix)
        if available <= 0:
            raise ValueError("摘要上下文预算不足以容纳消息来源标识，无法完整处理")
        text = cleaned_text_by_index[index]
        for offset in range(0, len(text), available):
            line = prefix + text[offset:offset + available]
            if lines and size + 1 + len(line) > max_chars:
                chunks.append(("\n".join(lines), tuple(dict.fromkeys(ids))))
                lines, ids, size = [], [], 0
            size += len(line) + bool(lines)
            lines.append(line)
            ids.append(message.msg_id)
    if lines:
        chunks.append(("\n".join(lines), tuple(dict.fromkeys(ids))))
    return chunks


def _evidence_ids(response, *, include_candidates=True):
    ids = list(response.overview_message_ids)
    for field in ("main_topics", "conclusions", "resources", "tasks", "open_questions", "candidates"):
        if field == "candidates" and not include_candidates:
            continue
        for item in getattr(response, field):
            if isinstance(item, EvidenceItem | CandidateOutput):
                ids.extend(item.message_ids)
    return set(ids)


def _claim_content(response):
    """Canonical content for citation-only repair, including legacy text claims."""
    content = response.model_dump()
    content.pop("overview_message_ids")
    for field in ("main_topics", "conclusions", "resources", "tasks", "open_questions", "candidates"):
        content[field] = [
            {key: value for key, value in item.items() if key != "message_ids"}
            if isinstance(item, dict) else {"text": item}
            for item in content[field]
        ]
    return content


def _missing_citations(response):
    if response.overview.strip() and not response.overview_message_ids:
        return True
    for field in ("main_topics", "conclusions", "resources", "tasks", "open_questions", "candidates"):
        for item in getattr(response, field):
            if isinstance(item, str):
                if item.strip():
                    return True
            elif not item.message_ids and any(
                value.strip() for key, value in item.model_dump().items()
                if key not in {"message_ids", "type"} and isinstance(value, str)
            ):
                return True
    return False


def _preserves_claim_citations(before, after):
    if not set(before.overview_message_ids).issubset(after.overview_message_ids):
        return False
    for field in ("main_topics", "conclusions", "resources", "tasks", "open_questions", "candidates"):
        for original, repaired in zip(getattr(before, field), getattr(after, field), strict=True):
            if isinstance(original, EvidenceItem | CandidateOutput) and not set(original.message_ids).issubset(getattr(repaired, "message_ids", [])):
                return False
    return True


def _deduplicate_response(response):
    updates = {}
    for field in ("main_topics", "conclusions", "resources", "tasks", "open_questions", "candidates"):
        unique = {}
        for item in getattr(response, field):
            content = item if isinstance(item, str) else item.model_dump(exclude={"message_ids"})
            key = json.dumps(content, ensure_ascii=False, sort_keys=True)
            if key in unique and not isinstance(item, str):
                previous = unique[key]
                item = item.model_copy(update={
                    "message_ids": list(dict.fromkeys(previous.message_ids + item.message_ids)),
                })
            unique[key] = item
        updates[field] = list(unique.values())
    return response.model_copy(update=updates)


def _validated_response(raw, group_id, allowed_ids):
    try:
        response = SummaryResponse.model_validate(raw)
    except ValidationError as exc:
        reject_response(raw)
        raise ValueError(f"AI响应结构无效: {exc}") from exc
    if response.group_id != group_id:
        reject_response(raw)
        raise ValueError("AI响应 group_id 不匹配")

    def valid_ids(ids):
        return list(dict.fromkeys(mid for mid in ids if mid in allowed_ids))

    updates = {"overview_message_ids": valid_ids(response.overview_message_ids)}
    for field in ("main_topics", "conclusions", "resources", "tasks", "open_questions", "candidates"):
        items = []
        for item in getattr(response, field):
            if isinstance(item, EvidenceItem | CandidateOutput):
                item = item.model_copy(update={"message_ids": valid_ids(item.message_ids)})
            items.append(item)
        updates[field] = items
    return response.model_copy(update=updates)


class Summarizer:
    """按 docs/ai 规范动态组装 prompt 的摘要生成器。"""

    def __init__(self, *, ai, max_context_chars: int):
        self.ai = ai
        self.max_context_chars = max_context_chars
        self.preprocessor = Preprocessor()

    def _generate(
        self, *, system_prompt, user_prompt, group_id, allowed_ids, evidence,
        required_ids=frozenset(), bounded_output=False,
    ):
        raw = self.ai.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        response = _validated_response(raw, group_id, allowed_ids)
        if _missing_citations(response):
            reject_response(raw)
            repair_evidence = json.dumps(
                {"summary": response.model_dump(), "evidence": evidence},
                ensure_ascii=False, separators=(",", ":"),
            )
            if len(repair_evidence) > self.max_context_chars:
                raise ValueError("摘要缺少真实引用，完整引用修复证据超过上下文预算；请提高上限后重试")
            raw = self.ai.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"群ID: {group_id}\n"
                    "仅修复引用：为 summary 中缺少来源的实质条目补充实际 evidence 支持的 message_ids；"
                    "不得改变、增加、删除或重排任何正文、条目及其他字段。"
                    "旧字符串结论或问题可转为 {text, message_ids}，text 必须逐字不变。"
                    "只允许引用本次 evidence 实际提供的来源 ID，不确定则留空，不能猜测。"
                    "已有合法引用应保留。返回完整同结构 JSON。\n引用修复输入：\n" + repair_evidence
                )},
            ])
            repaired = _validated_response(raw, group_id, allowed_ids)
            if _claim_content(response) != _claim_content(repaired):
                reject_response(raw)
                raise ValueError("AI引用修复改变了正文，未生成报告")
            if _missing_citations(repaired) or not _preserves_claim_citations(response, repaired):
                reject_response(raw)
                raise ValueError("AI引用修复后仍缺少合法来源，未生成报告")
            response = repaired
        if not required_ids.issubset(_evidence_ids(response, include_candidates=False)):
            reject_response(raw)
            raise ValueError("合并摘要遗漏了已验证的实质来源，未生成报告")
        response = _deduplicate_response(response)
        if bounded_output and len(response.model_dump_json()) > self.max_context_chars:
            reject_response(raw)
            raise ValueError("阶段完整摘要超过上下文预算，无法完整合并；请提高上下文上限后重试")
        return response

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
            for message in sorted(messages, key=lambda item: item.timestamp)
        ]
        source_texts = [
            message.text.strip() or message.raw_digest.strip() or message.message_type
            for message in local_messages
        ]
        cleaned = self.preprocessor.process(source_texts)
        cleaned_text_by_index = dict(
            zip(cleaned.kept_indexes, cleaned.cleaned_lines, strict=True)
        )
        contexts = _complete_contexts(local_messages, cleaned_text_by_index, self.max_context_chars)
        if not contexts:
            raise ValueError("清洗后没有可用于摘要的消息")
        deterministic = {
            "links": cleaned.links,
            "files": cleaned.files,
            "todos": cleaned.todos,
        }
        report_date = window_start.astimezone(timezone).date().isoformat()
        system_prompt = build_system_prompt(category)
        chunked = len(contexts) > 1
        if chunked:
            # Preserve candidates until all chronological evidence has been merged.
            system_prompt = system_prompt.replace(
                f"数量上限：{CANDIDATE_LIMITS.get(category, 1)} 条",
                "本阶段暂不设候选数量上限，最终结果再统一筛选。",
            )
            system_prompt += (
                "\n分块完整性规则：当前输入是按时间顺序排列的一部分。"
                "保留全部有实质信息的话题、来源、分歧、待解问题及后续回答线索；"
                "不得按重要性取前几条而丢弃独立实质内容。"
                "同一原始消息可能连续分段，来源 ID 不变，不得猜测尚未收到的片段。"
            )

        response = None
        for context, ids in contexts:
            user_prompt = build_user_prompt(
                group_id=group_id, group_name=group_name, category=category,
                report_date=report_date, window_start=window_start.isoformat(),
                window_end=window_end.isoformat(), message_count=len(ids),
                source_message_count=len(set(message.msg_id for message in messages)),
                discarded_message_count=cleaned.discarded_count, context=context,
                # Sources themselves carry extracted values in chunk mode. Do not
                # duplicate the whole archive in every bounded partial request.
                deterministic={} if chunked else deterministic,
                keywords=keywords, knowledge_base=knowledge_base,
                context_truncated=False,
            )
            if chunked:
                user_prompt = user_prompt.replace("数据质量: 上下文完整。", "数据质量: 当前分块完整；其余分块将按时间顺序合并。")
            partial = self._generate(
                system_prompt=system_prompt, user_prompt=user_prompt,
                group_id=group_id, allowed_ids=set(ids), evidence=context,
                bounded_output=chunked,
            )
            if response is None:
                response = partial
                continue
            evidence = json.dumps(
                [response.model_dump(), partial.model_dump()],
                ensure_ascii=False, separators=(",", ":"),
            )
            if len(evidence) > self.max_context_chars:
                raise ValueError("完整摘要的合并证据超过上下文预算；未截断，请提高上下文上限后重试")
            merge_prompt = (
                f"群ID: {group_id}\n"
                "请将以下两个摘要按时间顺序合并，前者较早，后者较晚。"
                "保留所有独立实质话题及其来源、参与者归属和真正分歧，语义去重；"
                "不得使用固定条数或重要性排名删除实质内容。"
                "后续回答、纠正或明确决定应更新较早状态：已回答的问题从 open_questions 移除，"
                "在对应话题或结论中保留问题及回答的真实来源；未解决的分歧继续保留。"
                "只能引用输入摘要实际携带的 message_ids，不能补入其他原始消息 ID，"
                "不得把摘要当成新的外部核实。保留候选供最终统一筛选。"
                "只返回相同结构的 JSON。\n待合并摘要（时间顺序）：\n" + evidence
            )
            response = self._generate(
                system_prompt=system_prompt, user_prompt=merge_prompt, group_id=group_id,
                allowed_ids=_evidence_ids(response) | _evidence_ids(partial), evidence=evidence,
                required_ids=_evidence_ids(response, include_candidates=False) | _evidence_ids(partial, include_candidates=False),
                bounded_output=True,
            )
        limit = CANDIDATE_LIMITS.get(category, 1)
        response = response.model_copy(update={"candidates": response.candidates[:limit]})
        included_ids = tuple(dict.fromkeys(mid for _, ids in contexts for mid in ids))
        source_ids = {message.msg_id for message in messages}
        return SummaryResult(
            response=response,
            deterministic=deterministic,
            context_chars=sum(len(context) for context, _ in contexts),
            context_truncated=False,
            source_messages=len(source_ids),
            included_messages=len(included_ids),
            discarded_messages=len(source_ids - set(included_ids)),
            included_message_ids=included_ids,
        )
