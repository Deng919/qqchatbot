from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class ReportPaths:
    markdown: Path
    json: Path


@dataclass
class PreparedReportWrite:
    paths: ReportPaths
    temporary_paths: dict[Path, Path]
    backup_paths: dict[Path, Path] = field(default_factory=dict)
    installed_paths: list[Path] = field(default_factory=list)
    closed: bool = False

    def install(self) -> None:
        if self.closed:
            raise RuntimeError("报告写入事务已经结束")
        try:
            for target in self.temporary_paths:
                if target.exists():
                    backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
                    shutil.copy2(target, backup)
                    self.backup_paths[target] = backup
            for target, temporary in self.temporary_paths.items():
                os.replace(temporary, target)
                self.installed_paths.append(target)
        except Exception:
            self.rollback()
            raise

    def rollback(self) -> None:
        if self.closed:
            return
        for target in reversed(self.installed_paths):
            backup = self.backup_paths.get(target)
            if backup is not None and backup.exists():
                os.replace(backup, target)
            else:
                target.unlink(missing_ok=True)
        self._cleanup()

    def finalize(self) -> None:
        if self.closed:
            return
        self._cleanup()

    def _cleanup(self) -> None:
        for temporary in self.temporary_paths.values():
            temporary.unlink(missing_ok=True)
        for backup in self.backup_paths.values():
            backup.unlink(missing_ok=True)
        self.closed = True


def render_markdown(
    *,
    group_name: str,
    report_date: str,
    window: str,
    topics: list[str | Mapping[str, str]],
    conclusions: list[str],
    resources: list[str | Mapping[str, str]],
    tasks: list[str],
    open_questions: list[str],
    deterministic: dict[str, list[str]],
    quality_note: str,
    overview: str = "",
    title_suffix: str = "日报",
    date_label: str = "日期",
) -> str:
    def bullets(items: list[str]) -> list[str]:
        return [f"- {item}" for item in items if item.strip()]

    def topic_line(item: str | Mapping[str, str]) -> str:
        if isinstance(item, str):
            return item
        title = item.get("topic", "").strip()
        summary = item.get("summary", "").strip()
        return f"**{title}**：{summary}" if summary else title

    def resource_line(item: str | Mapping[str, str]) -> str:
        if isinstance(item, str):
            return item
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        description = item.get("description", "").strip()
        label = f"[{title}]({url})" if url else title
        return f"{label}：{description}" if description else label

    lines = [
        f"# {group_name}{title_suffix}",
        "",
        f"- {date_label}：{report_date}",
        f"- 时间窗：{window}",
    ]
    repeated_overview = (
        len(topics) == 1
        and overview.strip() in topic_line(topics[0])
    )
    if overview.strip() and not repeated_overview:
        lines.extend(["", "## 今日概览", overview.strip()])

    for title, items in (
        ("主要话题", [topic_line(item) for item in topics]),
        ("重要结论", conclusions),
        ("资源与链接", [resource_line(item) for item in resources]),
        ("任务或承诺", tasks),
        ("未解决问题或争议", open_questions),
    ):
        content = bullets(items)
        if content:
            lines.extend(["", f"## {title}", *content])
    if quality_note.strip():
        lines.extend(["", "## 数据范围", quality_note.strip()])
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
        prepared = self.prepare_named(
            f"{report_date}__{group_id}", markdown, payload
        )
        try:
            prepared.install()
            prepared.finalize()
        except Exception:
            prepared.rollback()
            raise
        return prepared.paths

    def prepare_named(
        self, stem: str, markdown: str, payload: dict
    ) -> PreparedReportWrite:
        if not stem or Path(stem).name != stem or stem in {".", ".."}:
            raise ValueError("报告文件名无效")
        markdown_path = self.output_dir / f"{stem}.md"
        json_path = self.output_dir / f"{stem}.json"
        contents = {
            markdown_path: markdown,
            json_path: json.dumps(payload, ensure_ascii=False, indent=2),
        }
        temporary_paths: dict[Path, Path] = {}
        try:
            for target, content in contents.items():
                temporary_paths[target] = self._prepare_text(target, content)
        except Exception:
            for temporary in temporary_paths.values():
                temporary.unlink(missing_ok=True)
            raise
        return PreparedReportWrite(
            paths=ReportPaths(markdown=markdown_path, json=json_path),
            temporary_paths=temporary_paths,
        )

    @staticmethod
    def _prepare_text(target: Path, content: str) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            return Path(handle.name)


def published_report_is_valid(markdown_path: str | Path, json_path: str | Path) -> bool:
    """Return true only for a readable Markdown/JSON report pair."""
    try:
        Path(markdown_path).read_text(encoding="utf-8")
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)
