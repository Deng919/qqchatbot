"""Export confirmed QQ Digest knowledge candidates for MyCortex."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .candidate_context import candidate_source_context
from .candidates import CandidateService


FORMAT = "mycortex.qqdigest"
VERSION = 1
MAX_BODY_BYTES = 256 * 1024


def _source_uri(group_id: int, message_ids: list[str], candidate_id: int) -> str:
    if message_ids:
        return f"qqdigest://group/{group_id}/message/{message_ids[0]}"
    return f"qqdigest://group/{group_id}/candidate/{candidate_id}"


def _body(candidate, group_name: str, context: dict) -> str:
    message_ids = ", ".join(candidate.message_ids) or "无"
    lines = [
        f"# {candidate.title}",
        "",
        f"QQ Digest 候选 ID：{candidate.candidate_id}",
        f"候选类型：{candidate.candidate_type}",
        f"群：{group_name}（{candidate.group_id}）",
        f"关联消息 ID：{message_ids}",
        f"筛选理由：{candidate.reason}",
    ]
    if candidate.link:
        lines.append(f"链接：{candidate.link}")
    lines.extend(["", "## 候选正文", "", candidate.content or candidate.excerpt or candidate.reason])
    lines.extend(["", "## 原消息上下文", ""])
    for item in context["source_context"]:
        cited = "引用" if item["is_cited"] else "上下文"
        sender = item.get("sender_name") or item.get("sender_qq") or "未知发送者"
        text = item["text"]
        if item["text_truncated"]:
            text += "（正文已按 QQ Digest 上下文上限截断）"
        lines.append(f"- [{cited}] {item['timestamp']} {sender} / {item['msg_id']}：{text}")
    if not context["source_context"]:
        lines.append("- QQ Digest 未找到原消息正文；保留候选内容和候选 ID。")
    if context["source_context_truncated"]:
        lines.append("- 上下文已达到 QQ Digest 的 40 条上限。")
    if context["missing_source_count"]:
        lines.append(f"- 有 {context['missing_source_count']} 条引用消息已不在当前本地归档。")
    return "\n".join(lines).strip() + "\n"


def _observed_at(candidate, context: dict) -> str:
    cited = [item for item in context["source_context"] if item["is_cited"]]
    value = cited[0]["timestamp"] if cited else f"{candidate.created_date}T00:00:00+08:00"
    # Archive timestamps are ISO strings. Keep an explicit offset for MyCortex's contract.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def export_confirmed_candidates(archive, *, candidate_ids: list[int] | None = None) -> dict:
    """Build a portable bundle from actually published, confirmed candidates."""
    service = CandidateService(archive)
    params: list[object] = []
    where = "c.status='confirmed' AND EXISTS (SELECT 1 FROM knowledge_items k WHERE k.candidate_id=c.candidate_id)"
    if candidate_ids:
        placeholders = ",".join("?" for _ in candidate_ids)
        where += f" AND c.candidate_id IN ({placeholders})"
        params.extend(candidate_ids)
    rows = archive.connection.execute(
        "SELECT c.*, COALESCE(g.name, CAST(c.group_id AS TEXT)) AS group_name "
        "FROM candidates c LEFT JOIN groups g ON g.group_id=c.group_id "
        f"WHERE {where} ORDER BY c.candidate_id",
        params,
    ).fetchall()
    candidates = []
    for row in rows:
        candidate = service.get(int(row["candidate_id"]))
        context = candidate_source_context(archive, candidate)
        body = _body(candidate, row["group_name"], context)
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError(
                f"候选 {candidate.candidate_id} 导出正文超过 MyCortex 的 256 KiB 限制"
            )
        candidates.append(
            {
                "candidateId": candidate.candidate_id,
                "candidateRevision": candidate.revision,
                "requestKey": f"qqdigest:candidate:{candidate.candidate_id}",
                "title": candidate.title,
                "candidateType": candidate.candidate_type,
                "filterReason": candidate.reason,
                "groupId": candidate.group_id,
                "groupName": row["group_name"],
                "messageIds": candidate.message_ids,
                "body": body,
                "sourceUri": _source_uri(candidate.group_id, candidate.message_ids, candidate.candidate_id),
                "author": f"QQ Digest 群 {row['group_name']}",
                "observedAt": _observed_at(candidate, context),
                "context": context,
            }
        )
    return {
        "format": FORMAT,
        "version": VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "source": "qq-digest",
        "candidates": candidates,
    }


def write_bundle(archive, output: Path, *, candidate_ids: list[int] | None = None) -> dict:
    bundle = export_confirmed_candidates(archive, candidate_ids=candidate_ids)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(bundle, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"path": str(output.resolve()), "candidates": len(bundle["candidates"]), "format": FORMAT}
