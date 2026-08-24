from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


def write_item(path: Path, item: KnowledgeItem) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"条目 ID：{item.item_id}"
    if path.exists() and marker in path.read_text(encoding="utf-8"):
        return

    lines = [
        f"## {item.date} | {item.category}",
        f"- 来源：{item.source_group}",
        f"- 标题：{item.title}",
    ]
    if item.link:
        lines.append(f"- 链接：{item.link}")
    lines.extend(
        [
            f"- 价值：{item.value}",
            f"- 摘录：{item.excerpt}",
            f"- 报告：{item.report_path}" if item.report_path else "- 报告：",
            marker,
            "",
        ]
    )
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


class KnowledgeWriter:
    filenames = {
        "resource": "resources.md",
        "experience": "experiences.md",
    }

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def write(self, item: KnowledgeItem, item_type: str) -> Path:
        filename = self.filenames.get(item_type)
        if filename is None:
            raise ValueError("item_type 只支持 resource 或 experience")
        path = self.directory / filename
        write_item(path, item)
        return path
