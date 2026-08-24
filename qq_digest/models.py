from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


MessageKind = Literal["text", "link", "file", "reply", "forward", "system", "unknown"]
CandidateStatus = Literal["pending", "confirmed", "ignored", "later"]
CandidateType = Literal["resource", "experience"]


class NormalizedMessage(BaseModel):
    msg_id: str
    group_id: int
    sender_qq: int | None = None
    timestamp: datetime
    message_type: MessageKind = "text"
    text: str = ""
    content_json: dict = Field(default_factory=dict)
    raw_digest: str = ""
    source_id: str = ""
    device_id: str = ""
    collected_at: datetime


class GroupConfig(BaseModel):
    group_id: int
    name: str
    enabled: bool = True
    daily_summary: bool = True
    category: Literal["tech", "resource", "project", "study", "general"] = "general"
    template: Literal["concise", "detailed"] = "concise"
    keywords: list[str] = Field(default_factory=list)
    important_candidates: bool = True
    collection_window_days: int = 30


class SummaryCandidate(BaseModel):
    candidate_id: int
    group_id: int
    message_ids: list[str]
    created_date: str
    candidate_type: CandidateType
    title: str
    link: str = ""
    content: str = ""
    reason: str
    excerpt: str = ""
    status: CandidateStatus = "pending"
    ignore_reason: str = ""
