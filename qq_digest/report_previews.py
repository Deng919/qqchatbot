"""Small, deterministic previews extracted from existing report JSON."""

from __future__ import annotations

import json
from pathlib import Path


def load_report_preview(json_path: str | Path) -> dict:
    """Return only stored overview/topic text, including wrapped range reports."""
    empty = {"overview": "", "points": [], "available": False}
    try:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return empty
    if not isinstance(payload, dict):
        return empty
    if isinstance(payload.get("summary"), dict):
        payload = payload["summary"]

    def text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    overview = text(payload.get("overview"))
    points: list[str] = []
    topics = payload.get("main_topics")
    for topic in topics if isinstance(topics, list) else []:
        if not isinstance(topic, dict):
            continue
        title = text(topic.get("topic"))
        summary = text(topic.get("summary"))
        point = f"{title}：{summary}" if title and summary else title or summary
        if point:
            points.append(point)
        if len(points) == 3:
            break
    return {"overview": overview, "points": points, "available": bool(overview or points)}
