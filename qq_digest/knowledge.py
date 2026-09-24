from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_LINE_SEPARATOR_PATTERN = r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]"


def _single_line(value: str) -> str:
    return " ".join(re.split(_LINE_SEPARATOR_PATTERN, str(value)))


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: str
    date: str
    category: str
    source_group: str
    title: str
    link: str = ""
    value: str = ""
    excerpt: str = ""
    report_path: str = ""
    content: str = ""


def write_item(path: Path, item: KnowledgeItem) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"条目 ID：{_single_line(item.item_id)}"
    if path.exists() and marker in path.read_text(encoding="utf-8").splitlines():
        return

    lines = [
        f"## {_single_line(item.date)} | {_single_line(item.category)}",
        f"- 来源：{_single_line(item.source_group)}",
        f"- 标题：{_single_line(item.title)}",
    ]
    if item.link:
        lines.append(f"- 链接：{_single_line(item.link)}")
    lines.extend(
        [
            f"- 价值：{_single_line(item.value)}",
            f"- 摘录：{_single_line(item.excerpt)}",
            (
                f"- 报告：{_single_line(item.report_path)}"
                if item.report_path
                else "- 报告："
            ),
        ]
    )
    if item.content:
        # Indentation preserves the complete body as literal Markdown content,
        # including headings/code fences, without creating entry marker lines.
        lines.extend(["", "### 正文", ""])
        # Match every separator recognized by splitlines() above, retaining a
        # final empty line, so alternate newlines cannot forge entry markers.
        body_lines = re.split(_LINE_SEPARATOR_PATTERN, item.content)
        lines.extend("    " + line for line in body_lines)
        lines.append("")
    lines.extend([marker, ""])
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


class KnowledgeWriter:
    filenames = {
        "resource": "resources.md",
        "experience": "experiences.md",
    }

    def __init__(self, directory: Path | Mapping[str, Path]):
        if isinstance(directory, Mapping):
            self.targets = {key: Path(value) for key, value in directory.items()}
            missing = set(self.filenames) - set(self.targets)
            if missing:
                raise ValueError("知识库目标缺少: " + ", ".join(sorted(missing)))
            self.directory = self.targets["resource"].parent
        else:
            self.directory = Path(directory)
            self.targets = {
                item_type: self.directory / filename
                for item_type, filename in self.filenames.items()
            }

    def path_for(self, item_type: str) -> Path:
        path = self.targets.get(item_type)
        if path is None:
            raise ValueError("item_type 只支持 resource 或 experience")
        return path

    def write(self, item: KnowledgeItem, item_type: str) -> Path:
        path = self.path_for(item_type)
        write_item(path, item)
        return path
