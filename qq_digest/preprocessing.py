from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
FILE_PATTERN = re.compile(r"\[(?:文件|图片|视频|音频)\]\s*(.++)")
TODO_PATTERN = re.compile(
    r"(TODO[:：]|待办|(?:^|[，,。；;]\s*)记得\s*"
    r"(?:(?:明天|后天|下周|今天|今晚)\s*)?(?:去|把)?"
    r"(?:提交|整理|修复|发送|发|补充|补|确认|更新|检查|联系|处理|完成)"
    r"|我(来|会|去)?(整理|修复|发|补|确认))",
    re.IGNORECASE,
)
NOISE_PATTERN = re.compile(r"^(有人加入了?本群|.*撤回了一条消息|.*加入了?本群)$")
TRAILING_URL_PUNCTUATION = ".,!?;:，。！？；：、）)]}》”'"
INTERNAL_QQ_HOSTS = {"tianquan.gtimg.cn", "zb.vip.qq.com"}


def normalize_url(value: str) -> str:
    value = html.unescape(value).rstrip(TRAILING_URL_PUNCTUATION)
    try:
        parts = urlsplit(value)
        hostname = (parts.hostname or "").lower()
        if not hostname:
            return value
        userinfo = ""
        if parts.username:
            userinfo = parts.username
            if parts.password:
                userinfo += f":{parts.password}"
            userinfo += "@"
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return value
    netloc = f"{userinfo}{hostname}{port}"
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment)
    )


def is_internal_qq_url(value: str) -> bool:
    try:
        return (urlsplit(value).hostname or "").lower() in INTERNAL_QQ_HOSTS
    except ValueError:
        return False


@dataclass
class PreprocessResult:
    cleaned_lines: list[str] = field(default_factory=list)
    kept_indexes: list[int] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    discarded_count: int = 0


class Preprocessor:
    def clean_line(self, raw: str) -> str | None:
        line = re.sub(r"\s+", " ", html.unescape(raw)).strip()
        if not line or NOISE_PATTERN.match(line):
            return None
        urls = [normalize_url(value) for value in URL_PATTERN.findall(line)]
        without_urls = URL_PATTERN.sub("", line).strip(
            TRAILING_URL_PUNCTUATION + " "
        )
        if urls and all(is_internal_qq_url(value) for value in urls) and not without_urls:
            return None
        return line

    def process(self, lines: list[str]) -> PreprocessResult:
        result = PreprocessResult()
        for index, raw in enumerate(lines):
            line = self.clean_line(raw)
            if line is None:
                result.discarded_count += 1
                continue
            result.cleaned_lines.append(line)
            result.kept_indexes.append(index)
            result.links.extend(
                url
                for url in map(normalize_url, URL_PATTERN.findall(line))
                if not is_internal_qq_url(url)
            )
            file_match = FILE_PATTERN.match(line)
            if file_match:
                result.files.append(file_match.group(1).strip())
            if TODO_PATTERN.search(line):
                result.todos.append(line)
        result.links = list(dict.fromkeys(result.links))
        result.files = list(dict.fromkeys(result.files))
        return result
