from __future__ import annotations

import re
from dataclasses import dataclass, field


URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
FILE_PATTERN = re.compile(r"\[(?:文件|图片|视频|音频)\]\s*(.++)")
TODO_PATTERN = re.compile(r"(TODO[:：]|待办|记得|我(来|会|去)?(整理|修复|发|补|确认))", re.IGNORECASE)
NOISE_PATTERN = re.compile(r"^(有人加入了?本群|.*撤回了一条消息|.*加入了?本群)$")


@dataclass
class PreprocessResult:
    cleaned_lines: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    discarded_count: int = 0


class Preprocessor:
    def process(self, lines: list[str]) -> PreprocessResult:
        result = PreprocessResult()
        for raw in lines:
            line = re.sub(r"\s+", " ", raw).strip()
            if not line or NOISE_PATTERN.match(line):
                result.discarded_count += 1
                continue
            result.cleaned_lines.append(line)
            result.links.extend(URL_PATTERN.findall(line))
            file_match = FILE_PATTERN.match(line)
            if file_match:
                result.files.append(file_match.group(1).strip())
            if TODO_PATTERN.search(line):
                result.todos.append(line)
        result.links = list(dict.fromkeys(result.links))
        result.files = list(dict.fromkeys(result.files))
        return result
