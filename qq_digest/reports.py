from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReportPaths:
    markdown: Path
    json: Path


def render_markdown(
    *,
    group_name: str,
    report_date: str,
    window: str,
    topics: list[str],
    conclusions: list[str],
    resources: list[str],
    tasks: list[str],
    open_questions: list[str],
    deterministic: dict[str, list[str]],
    quality_note: str,
) -> str:
    def bullets(items: list[str]) -> list[str]:
        return [f"- {item}" for item in items] or ["- 无"]

    lines = [
        f"# {group_name}日报",
        "",
        f"- 日期：{report_date}",
        f"- 时间窗：{window}",
        "",
        "## 主要话题",
        *bullets(topics),
        "",
        "## 重要结论",
        *bullets(conclusions),
        "",
        "## 资源与链接",
        *bullets(resources),
        "",
        "## 任务或承诺",
        *bullets(tasks),
        "",
        "## 未解决问题或争议",
        *bullets(open_questions),
        "",
        "## 确定性提取",
        f"- 链接：{', '.join(deterministic.get('links', [])) or '无'}",
        f"- 文件：{', '.join(deterministic.get('files', [])) or '无'}",
        f"- 待办：{', '.join(deterministic.get('todos', [])) or '无'}",
    ]
    if quality_note:
        lines.extend(["", "## 数据质量", quality_note])
    return "\n".join(lines) + "\n"


class ReportWriter:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        *,
        group_id: int,
        group_name: str,
        report_date: str,
        markdown: str,
        payload: dict,
    ) -> ReportPaths:
        markdown_path = self.output_dir / f"{report_date}__{group_id}.md"
        json_path = self.output_dir / f"{report_date}__{group_id}.json"
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ReportPaths(markdown=markdown_path, json=json_path)
