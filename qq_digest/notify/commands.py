"""QQ Bot 指令处理：WebSocket 监听、白名单鉴权、候选审核指令。

支持中英文指令：
- /list 或 候选    列出待审核候选
- /confirm <id...> 或 入库 <id...>  确认候选入库
- /ignore <id> [reason]  忽略候选
- /later <id>  稍后审核
- /view <id>  查看候选详情
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .notifier import QQBotNotifier
from ..candidates import CandidateService
from ..knowledge import KnowledgeWriter, KnowledgeItem


@dataclass(frozen=True)
class CommandResult:
    success: bool
    message: str


class BotCommandHandler:
    """处理 QQ Bot 私信指令。"""

    COMMAND_PATTERN = re.compile(r"^/(\w+)(?:\s+(.*))?$")
    CHINESE_ALIASES = {
        "候选": "list",
        "入库": "confirm",
        "忽略": "ignore",
        "稍后": "later",
        "查看": "view",
    }

    def __init__(
        self,
        *,
        notifier: QQBotNotifier,
        candidates: CandidateService,
        knowledge: KnowledgeWriter,
        group_names: dict[int, str] | None = None,
    ):
        self.notifier = notifier
        self.candidates = candidates
        self.knowledge = knowledge
        self.group_names = group_names or {}

    def _group_name(self, group_id: int) -> str:
        for group in self.candidates.archive.all_groups():
            if group.group_id == group_id:
                return group.name
        return self.group_names.get(group_id, str(group_id))

    def handle_message(self, openid: str, content: str) -> CommandResult:
        """处理一条收到的私信指令。"""
        if not self.notifier.is_authorized(openid):
            return CommandResult(False, "未授权")

        text = content.strip()
        match = self.COMMAND_PATTERN.match(text)
        if match:
            cmd = match.group(1).lower()
            args = match.group(2) or ""
        else:
            parts = text.split(None, 1)
            cmd = self.CHINESE_ALIASES.get(parts[0] if parts else "", "")
            args = parts[1] if len(parts) > 1 else ""
            if not cmd:
                return CommandResult(
                    False,
                    "无法识别指令，可用：候选、入库、忽略、稍后、查看",
                )

        handlers = {
            "list": self._cmd_list,
            "confirm": self._cmd_confirm,
            "ignore": self._cmd_ignore,
            "later": self._cmd_later,
            "view": self._cmd_view,
        }

        handler = handlers.get(cmd)
        if not handler:
            return CommandResult(False, f"未知指令 /{cmd}")

        return handler(openid, args)

    def _cmd_list(self, openid: str, args: str) -> CommandResult:
        pending = self.candidates.pending()
        if not pending:
            return CommandResult(True, "当前无待审核候选")

        lines = [f"待审核候选（{len(pending)} 条）："]
        for c in pending[:10]:
            group_name = self._group_name(c.group_id)
            lines.append(f"#{c.candidate_id} [{c.candidate_type}] {c.title}（{group_name}）")
        if len(pending) > 10:
            lines.append(f"...共 {len(pending)} 条，仅显示前 10 条")
        return CommandResult(True, "\n".join(lines))

    def _cmd_confirm(self, openid: str, args: str) -> CommandResult:
        raw_ids = args.split()
        if not raw_ids:
            return CommandResult(False, "用法：入库 <候选ID> [更多ID]")
        try:
            candidate_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except ValueError:
            return CommandResult(False, "候选ID 必须是数字")

        confirmed: list[int] = []
        failed: list[tuple[int, str]] = []
        for candidate_id in candidate_ids:
            try:
                self._confirm_one(candidate_id)
                confirmed.append(candidate_id)
            except (KeyError, ValueError, OSError) as exc:
                failed.append((candidate_id, str(exc)))

        if not failed:
            if len(confirmed) == 1:
                candidate = self.candidates.get(confirmed[0])
                return CommandResult(True, f"已入库：{candidate.title}")
            return CommandResult(True, f"已入库 {len(confirmed)} 条：{', '.join(map(str, confirmed))}")

        failure_text = "；".join(f"#{candidate_id} {reason}" for candidate_id, reason in failed)
        return CommandResult(
            False,
            f"已入库 {len(confirmed)} 条；失败 {len(failed)} 条：{failure_text}",
        )

    def _confirm_one(self, cid: int) -> None:
        candidate = self.candidates.get(cid)
        group_name = self._group_name(candidate.group_id)

        item = KnowledgeItem(
            item_id=str(cid),
            date=candidate.created_date,
            category="资源" if candidate.candidate_type == "resource" else "经验",
            source_group=group_name,
            title=candidate.title,
            link=candidate.link,
            value=candidate.reason,
            excerpt=candidate.excerpt,
            content=candidate.content,
        )
        path = self.knowledge.write(item, candidate.candidate_type)
        self.candidates.archive.record_knowledge_item(
            item_id=item.item_id,
            candidate_id=cid,
            markdown_path=str(path),
        )
        self.candidates.confirm(cid)

    def _cmd_ignore(self, openid: str, args: str) -> CommandResult:
        parts = args.split(None, 1)
        if not parts:
            return CommandResult(False, "用法：/ignore <候选ID> [原因]")
        try:
            cid = int(parts[0])
        except ValueError:
            return CommandResult(False, "候选ID 必须是数字")
        reason = parts[1] if len(parts) > 1 else ""
        try:
            self.candidates.ignore(cid, reason)
        except KeyError as exc:
            return CommandResult(False, str(exc))
        return CommandResult(True, f"已忽略候选 #{cid}")

    def _cmd_later(self, openid: str, args: str) -> CommandResult:
        try:
            cid = int(args.strip())
        except ValueError:
            return CommandResult(False, "用法：/later <候选ID>")
        try:
            self.candidates.update_status(cid, "later")
        except KeyError as exc:
            return CommandResult(False, str(exc))
        return CommandResult(True, f"已标记稍后审核 #{cid}")

    def _cmd_view(self, openid: str, args: str) -> CommandResult:
        try:
            cid = int(args.strip())
        except ValueError:
            return CommandResult(False, "用法：/view <候选ID>")

        try:
            c = self.candidates.get(cid)
        except KeyError as exc:
            return CommandResult(False, str(exc))
        group_name = self._group_name(c.group_id)
        lines = [
            f"候选 #{cid}",
            f"标题：{c.title}",
            f"类型：{c.candidate_type}",
            f"来源群：{group_name}",
            f"原因：{c.reason}",
        ]
        if c.link:
            lines.append(f"链接：{c.link}")
        if c.content:
            lines.append(f"内容：{c.content}")
        if c.excerpt:
            lines.append(f"摘录：{c.excerpt}")
        return CommandResult(True, "\n".join(lines))
