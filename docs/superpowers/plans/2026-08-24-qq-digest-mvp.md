# QQ Digest MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个可测试、可运行的 QQ 群消息归档、每日摘要、重点信息审核和 Markdown 入库闭环。

**Architecture:** 第一阶段实现 collector 与 QQNT 私有实现解耦的 `NormalizedMessage` 流水线，使用 SQLite 作为唯一业务状态源，FastAPI 提供带登录的 Web 审核 UI，OpenAI-compatible API 只接收有长度限制的摘要上下文。真实 QQNT 数据库解密/protobuf 解析和官方 QQ Bot 通知是独立后续计划，本计划通过 adapter 接口和假数据测试把它们隔离。

**Tech Stack:** Python 3.11+，pytest，SQLite 3，Pydantic，PyYAML，FastAPI，HTTPX，Jinja2。

---

## 阶段范围

本计划交付以下可运行功能：

1. 配置加载、校验和密钥安全处理。
2. SQLite 归档库和消息幂等入库。
3. 消息预处理器：文本、链接、文件、回复、转发摘要和系统噪声。
4. OpenAI-compatible 摘要客户端和响应结构校验。
5. Markdown/JSON 每日报告生成。
6. 重点信息候选队列。
7. Web 登录和候选审核。
8. Markdown 知识库幂等写入。
9. 命令行入口和 doctor 的基础检查。
10. 定时同步与每日摘要调度。

群选择在 MVP 中通过 `config/config.yaml` 维护；因为“扫描本地群并勾选”依赖真实 QQNT 采集器，Web 群选择页面放到 QQNT Adapter 计划完成后实现。

不包含：

- 真实 QQNT 数据库定位、密钥提取、解密和 protobuf 解析。
- 官方 QQ Bot access token、私信投递和 WebSocket 指令。
- 媒体文件本体归档。
- 服务器同步。

后续计划应分别命名为：

- `2026-MM-DD-qqnt-adapter.md`
- `2026-MM-DD-official-qq-bot.md`

## 文件结构

```text
qq_digest/
  __init__.py
  config.py
  models.py
  archive.py
  preprocessing.py
  summary.py
  reports.py
  candidates.py
  knowledge.py
  cli.py
  scheduler.py
  collector/
    __init__.py
    base.py
    fixture.py
  ai/
    __init__.py
    client.py
  web/
    __init__.py
    app.py
    auth.py
    templates/
      login.html
      candidates.html
  tests/
    conftest.py
    test_config.py
    test_models.py
    test_archive.py
    test_preprocessing.py
    test_ai_client.py
    test_summary.py
    test_reports.py
    test_candidates.py
    test_knowledge.py
    test_web.py
    test_cli.py
    test_scheduler.py
  config/
    config.example.yaml
  pyproject.toml
  .gitignore
```

每个文件职责：

- `models.py`：所有跨模块数据结构。
- `config.py`：配置 schema、默认值、环境变量覆盖和路径解析。
- `archive.py`：SQLite schema、事务、幂等写入和查询。
- `preprocessing.py`：确定性提取和噪声过滤。
- `ai/client.py`：OpenAI-compatible HTTP 调用与失败分类。
- `summary.py`：上下文裁剪、提示词、响应校验和报告数据组装。
- `reports.py`：Markdown/JSON 文件输出。
- `candidates.py`：候选创建与审核状态流转。
- `knowledge.py`：Markdown 条目渲染与幂等追加。
- `collector/base.py`：采集适配器接口。
- `collector/fixture.py`：测试和示例数据采集器。
- `web/app.py`：路由、表单、登录和群配置。
- `web/auth.py`：密码散列、签名会话 Cookie。
- `cli.py`：doctor/sync/summarize/serve/backfill 命令。
- `scheduler.py`：周期任务循环。

---

### Task 1: 项目骨架与配置模型

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `qq_digest/__init__.py`
- Create: `qq_digest/models.py`
- Create: `qq_digest/config.py`
- Create: `config/config.example.yaml`
- Test: `tests/conftest.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写配置失败测试**

```python
# tests/test_config.py
from pathlib import Path

import pytest
import yaml

from qq_digest.config import Config, ConfigError, load_config


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def base_config(tmp_path: Path) -> dict:
    return {
        "security": {"web_password_hash": "x" * 32},
        "groups": [],
        "ai": {
            "base_url": "https://api.example.com/v1",
            "model": "gpt-test",
            "api_key_env": "QQ_DIGEST_AI_API_KEY",
        },
    }


def test_load_config_resolves_paths_and_defaults(tmp_path):
    raw = base_config(tmp_path)
    raw["groups"] = [
        {
            "group_id": 123,
            "name": "测试群",
            "enabled": True,
            "daily_summary": True,
            "template": "concise",
            "keywords": ["网站"],
            "important_candidates": True,
            "collection_window_days": 30,
        }
    ]
    path = write_config(tmp_path, raw)
    config = load_config(path)

    assert config.data_dir == tmp_path
    assert config.archive_path == tmp_path / "archive" / "archive.sqlite"
    assert config.report_dir == tmp_path / "reports"
    assert config.knowledge_dir == tmp_path / "knowledge"
    assert config.groups[0].name == "测试群"
    assert config.summary.hour == 22
    assert config.summary.window_mode == "today"


def test_load_config_rejects_missing_ai_model(tmp_path):
    raw = base_config(tmp_path)
    raw["ai"].pop("model")
    path = write_config(tmp_path, raw)

    with pytest.raises(ConfigError, match="ai.model"):
        load_config(path)


def test_load_config_rejects_duplicate_groups(tmp_path):
    raw = base_config(tmp_path)
    raw["groups"] = [
        {"group_id": 123, "name": "群一"},
        {"group_id": 123, "name": "群二"},
    ]
    path = write_config(tmp_path, raw)

    with pytest.raises(ConfigError, match="重复"):
        load_config(path)


def test_load_config_accepts_extra_fields(tmp_path):
    raw = base_config(tmp_path)
    raw["collector"] = {"type": "fixture"}
    path = write_config(tmp_path, raw)

    assert load_config(path).data_dir == tmp_path
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: `ModuleNotFoundError: No module named 'qq_digest'`

- [ ] **Step 3: 创建项目骨架和配置实现**

```toml
# pyproject.toml
[project]
name = "qq-digest"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "httpx>=0.27",
  "jinja2>=3.1",
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "uvicorn>=0.30",
]

[project.scripts]
qq-digest = "qq_digest.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

```text
# .gitignore
.venv/
__pycache__/
.pytest_cache/
config/config.yaml
archive/
reports/
knowledge/
work/
logs/
.env
```

```python
# qq_digest/__init__.py
__version__ = "0.1.0"
```

```python
# qq_digest/models.py
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
```

```python
# qq_digest/config.py
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class ConfigError(ValueError):
    pass


class SummaryConfig(BaseModel):
    hour: int = 22
    minute: int = 0
    window_mode: str = "today"
    timezone: str = "Asia/Shanghai"


class AIConfig(BaseModel):
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 120
    max_context_chars: int = 60000


class SecurityConfig(BaseModel):
    web_password_hash: str
    session_secret_env: str = "QQ_DIGEST_SESSION_SECRET"
    session_hours: int = 12


class Config(BaseModel):
    model_config = {"extra": "allow"}

    data_dir: Path
    archive_path: Path
    report_dir: Path
    knowledge_dir: Path
    work_dir: Path
    log_dir: Path
    security: SecurityConfig
    summary: SummaryConfig = SummaryConfig()
    ai: AIConfig
    groups: list[GroupConfig] = Field(default_factory=list)

    def resolve_api_key(self) -> str:
        value = os.environ.get(self.ai.api_key_env, "")
        if not value:
            raise ConfigError(f"环境变量 {self.ai.api_key_env} 未设置")
        return value


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"无法读取配置: {exc}") from exc

    data_dir = path.parent.parent.resolve()
    raw.setdefault("summary", {})
    raw["data_dir"] = str(data_dir)
    raw["archive_path"] = str(data_dir / "archive" / "archive.sqlite")
    raw["report_dir"] = str(data_dir / "reports")
    raw["knowledge_dir"] = str(data_dir / "knowledge")
    raw["work_dir"] = str(data_dir / "work")
    raw["log_dir"] = str(data_dir / "logs")

    security_raw = raw.get("security") or {}
    if not security_raw.get("web_password_hash"):
        raise ConfigError("security.web_password_hash 必填")
    if not raw.get("ai", {}).get("model"):
        raise ConfigError("ai.model 必填")

    groups_raw = raw.get("groups", [])
    group_ids = [item["group_id"] for item in groups_raw]
    if len(group_ids) != len(set(group_ids)):
        raise ConfigError("groups.group_id 存在重复")

    try:
        config = Config.model_validate(raw)
    except Exception as exc:
        raise ConfigError(str(exc)) from exc

    if config.summary.window_mode not in {"today", "previous_day"}:
        raise ConfigError("summary.window_mode 只支持 today 或 previous_day")

    config.archive_path.parent.mkdir(parents=True, exist_ok=True)
    config.report_dir.mkdir(parents=True, exist_ok=True)
    config.knowledge_dir.mkdir(parents=True, exist_ok=True)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    return config
```

需要在 `qq_digest/config.py` 顶部补充：

```python
from .models import GroupConfig
```

```yaml
# config/config.example.yaml
summary:
  hour: 22
  minute: 0
  window_mode: today
  timezone: Asia/Shanghai

security:
  web_password_hash: "请用 qq-digest hash-password 生成"
  session_secret_env: QQ_DIGEST_SESSION_SECRET
  session_hours: 12

ai:
  base_url: https://api.example.com/v1
  model: your-model
  api_key_env: QQ_DIGEST_AI_API_KEY
  timeout_seconds: 120
  max_context_chars: 60000

groups: []
```

- [ ] **Step 4: 运行配置和模型测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_config.py tests/test_models.py -v`

Expected: 4 passed。不要创建空的 `tests/test_models.py`；跨模块数据结构已由配置、归档、摘要和集成测试约束，避免无意义测试占位。

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml .gitignore qq_digest config/config.example.yaml tests
git commit -m "feat: add project configuration foundation"
```

---

### Task 2: SQLite 归档库与幂等入库

**Files:**

- Create: `qq_digest/archive.py`
- Test: `tests/test_archive.py`
- Test: `tests/conftest.py`

- [ ] **Step 1: 写归档失败测试**

```python
# tests/test_archive.py
from datetime import datetime, timedelta

from qq_digest.archive import Archive
from qq_digest.models import NormalizedMessage


def make_message(msg_id="m1", timestamp=datetime(2026, 8, 24, 10, 0)):
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=123,
        sender_qq=10001,
        timestamp=timestamp,
        text="消息",
        collected_at=datetime(2026, 8, 24, 11, 0),
    )


def test_ingest_is_idempotent(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    result = archive.ingest([make_message()])
    result_again = archive.ingest([make_message()])

    assert result.inserted == 1
    assert result_again.inserted == 0
    assert archive.count_messages(group_id=123) == 1


def test_ingest_accepts_out_of_order_messages(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    base = datetime(2026, 8, 24, 10, 0)
    messages = [make_message("later", base + timedelta(hours=1)), make_message("earlier", base)]
    result = archive.ingest(messages)

    assert result.inserted == 2
    assert [m.msg_id for m in archive.messages_between(123, base, base + timedelta(hours=1))] == [
        "earlier",
        "later",
    ]


def test_ingest_rejects_duplicate_msg_id_in_same_batch(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")

    try:
        archive.ingest([make_message(), make_message()])
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert archive.count_messages(group_id=123) == 0
```

更新 `tests/conftest.py`：

```python
import pytest


@pytest.fixture
def message_factory():
    from tests.test_archive import make_message

    return make_message
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_archive.py -v`

Expected: `ImportError: cannot import name 'Archive'`

- [ ] **Step 3: 实现 Archive**

```python
# qq_digest/archive.py
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import NormalizedMessage


@dataclass(frozen=True)
class IngestResult:
    inserted: int
    skipped: int


class Archive:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @classmethod
    def open(cls, path: Path) -> "Archive":
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        return cls(connection)

    @contextmanager
    def transaction(self):
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate(self) -> None:
        with self.transaction():
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    daily_summary INTEGER NOT NULL DEFAULT 1,
                    template TEXT NOT NULL DEFAULT 'concise',
                    keywords TEXT NOT NULL DEFAULT '[]',
                    important_candidates INTEGER NOT NULL DEFAULT 1,
                    collection_window_days INTEGER NOT NULL DEFAULT 30
                );
                CREATE TABLE IF NOT EXISTS messages (
                    msg_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL,
                    sender_qq INTEGER,
                    timestamp TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    raw_digest TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, msg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_group_time
                    ON messages(group_id, timestamp);
                CREATE TABLE IF NOT EXISTS sync_state (
                    group_id INTEGER PRIMARY KEY,
                    last_timestamp TEXT,
                    backfill_completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    report_date TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(group_id, report_date)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    message_ids TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    candidate_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ignore_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_status
                    ON candidates(status, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    item_id TEXT PRIMARY KEY,
                    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id),
                    markdown_path TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS send_log (
                    send_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER REFERENCES reports(report_id),
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    attempted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def upsert_groups(self, groups: list) -> None:
        with self.transaction():
            for group in groups:
                self.connection.execute(
                    """
                    INSERT INTO groups(
                        group_id, name, enabled, daily_summary, template,
                        keywords, important_candidates, collection_window_days
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        name=excluded.name,
                        enabled=excluded.enabled,
                        daily_summary=excluded.daily_summary,
                        template=excluded.template,
                        keywords=excluded.keywords,
                        important_candidates=excluded.important_candidates,
                        collection_window_days=excluded.collection_window_days
                    """,
                    (
                        group.group_id,
                        group.name,
                        int(group.enabled),
                        int(group.daily_summary),
                        group.template,
                        json.dumps(group.keywords, ensure_ascii=False),
                        int(group.important_candidates),
                        group.collection_window_days,
                    ),
                )

    def ingest(self, messages: Iterable[NormalizedMessage]) -> IngestResult:
        rows = list(messages)
        keys = [(row.group_id, row.msg_id) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("同一批消息中存在重复 group_id/msg_id")

        inserted = 0
        skipped = 0
        with self.transaction():
            for message in rows:
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO messages(
                        msg_id, group_id, sender_qq, timestamp, message_type, text,
                        content_json, raw_digest, source_id, device_id, collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.msg_id,
                        message.group_id,
                        message.sender_qq,
                        message.timestamp.isoformat(),
                        message.message_type,
                        message.text,
                        json.dumps(message.content_json, ensure_ascii=False),
                        message.raw_digest,
                        message.source_id,
                        message.device_id,
                        message.collected_at.isoformat(),
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    skipped += 1
        return IngestResult(inserted=inserted, skipped=skipped)

    def _row_to_message(self, row: sqlite3.Row) -> NormalizedMessage:
        return NormalizedMessage(
            msg_id=row["msg_id"],
            group_id=row["group_id"],
            sender_qq=row["sender_qq"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            message_type=row["message_type"],
            text=row["text"],
            content_json=json.loads(row["content_json"]),
            raw_digest=row["raw_digest"],
            source_id=row["source_id"],
            device_id=row["device_id"],
            collected_at=datetime.fromisoformat(row["collected_at"]),
        )

    def messages_between(
        self, group_id: int, start: datetime, end: datetime
    ) -> list[NormalizedMessage]:
        rows = self.connection.execute(
            """
            SELECT * FROM messages
            WHERE group_id=? AND timestamp>=? AND timestamp<=?
            ORDER BY timestamp, msg_id
            """,
            (group_id, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def count_messages(self, group_id: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE group_id=?", (group_id,)
        ).fetchone()
        return int(row["total"])

    def close(self) -> None:
        self.connection.close()
```

- [ ] **Step 4: 运行归档测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_archive.py -v`

Expected: 3 passed

- [ ] **Step 5: Commit**

```powershell
git add qq_digest/archive.py tests/test_archive.py tests/conftest.py
git commit -m "feat: add idempotent sqlite archive"
```

---

### Task 3: 消息预处理和确定性提取

**Files:**

- Create: `qq_digest/preprocessing.py`
- Test: `tests/test_preprocessing.py`

- [ ] **Step 1: 写预处理失败测试**

```python
# tests/test_preprocessing.py
from qq_digest.preprocessing import Preprocessor, PreprocessResult


def test_extracts_links_and_files():
    processor = Preprocessor()
    result = processor.process(
        [
            "看这个 https://example.com/a?b=1 很好用",
            "[文件] 白皮书.pdf",
        ]
    )

    assert result.links == ["https://example.com/a?b=1"]
    assert result.files == ["白皮书.pdf"]


def test_filters_system_noise_and_normalizes_whitespace():
    processor = Preprocessor()
    result = processor.process(["有人加入本群", "  hello   world  "])

    assert result.cleaned_lines == ["hello world"]
    assert result.discarded_count == 1


def test_extracts_todo_statements():
    processor = Preprocessor()
    result = processor.process(["明天我整理一下链接", "TODO: 修复配置"])

    assert result.todos == ["明天我整理一下链接", "TODO: 修复配置"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_preprocessing.py -v`

Expected: `ModuleNotFoundError: No module named 'qq_digest.preprocessing'`

- [ ] **Step 3: 实现 Preprocessor**

```python
# qq_digest/preprocessing.py
from __future__ import annotations

import re
from dataclasses import dataclass, field


URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
FILE_PATTERN = re.compile(r"\[(?:文件|图片|视频|音频)\]\s*(.++)")
TODO_PATTERN = re.compile(r"(TODO[:：]|待办|记得|我(来|会|去)?(整理|修复|发|补|确认))", re.IGNORECASE)
NOISE_PATTERN = re.compile(r"^(有人加入了本群|.*撤回了一条消息|.*加入了本群)$")


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
```

- [ ] **Step 4: 运行预处理测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_preprocessing.py -v`

Expected: 3 passed

- [ ] **Step 5: Commit**

```powershell
git add qq_digest/preprocessing.py tests/test_preprocessing.py
git commit -m "feat: add message preprocessing"
```

---

### Task 4: OpenAI-compatible AI 客户端

**Files:**

- Create: `qq_digest/ai/__init__.py`
- Create: `qq_digest/ai/client.py`
- Test: `tests/test_ai_client.py`

- [ ] **Step 1: 写 AI 客户端失败测试**

```python
# tests/test_ai_client.py
import json

import httpx
import pytest

from qq_digest.ai.client import AIClient, AIError


def test_chat_sends_openai_compatible_request(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]},
        )
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
    )
    response = client.chat([{"role": "user", "content": "hi"}])

    assert response == {"ok": True}
    request = respx_mock.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"


def test_chat_raises_ai_error_on_http_failure(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server error")
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
    )

    with pytest.raises(AIError, match="500"):
        client.chat([{"role": "user", "content": "hi"}])
```

`pyproject.toml` 增加：

```toml
[project.optional-dependencies]
test = ["pytest>=8", "respx>=0.21"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_ai_client.py -v`

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现 AIClient**

```python
# qq_digest/ai/client.py
from __future__ import annotations

import json
from typing import Any

import httpx


class AIError(RuntimeError):
    pass


class AIClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            response = self._client.post(
                "/chat/completions",
                json={"model": self.model, "messages": messages, "temperature": 0.2},
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise AIError(f"AI 调用失败: {exc}") from exc

    def close(self) -> None:
        self._client.close()
```

```python
# qq_digest/ai/__init__.py
from .client import AIClient, AIError

__all__ = ["AIClient", "AIError"]
```

- [ ] **Step 4: 运行 AI 客户端测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_ai_client.py -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml qq_digest/ai tests/test_ai_client.py
git commit -m "feat: add openai-compatible client"
```

---

### Task 5: 摘要生成与结构校验

**Files:**

- Create: `qq_digest/summary.py`
- Test: `tests/test_summary.py`

- [ ] **Step 1: 写摘要失败测试**

```python
# tests/test_summary.py
from datetime import datetime

import pytest

from qq_digest.models import NormalizedMessage
from qq_digest.summary import Summarizer, SummaryResult, build_context


def message(msg_id, timestamp, text):
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=123,
        sender_qq=10001,
        timestamp=timestamp,
        text=text,
        collected_at=datetime(2026, 8, 24, 12, 0),
    )


class FakeAI:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.response


def valid_response():
    return {
        "group_id": 123,
        "main_topics": [{"topic": "工具推荐", "summary": "推荐了一个站点"}],
        "conclusions": ["站点适合归档"],
        "resources": [{"title": "站点", "link": "https://example.com"}],
        "tasks": [],
        "open_questions": [],
        "candidates": [
            {
                "type": "resource",
                "title": "站点",
                "link": "https://example.com",
                "content": "",
                "reason": "高质量教程站",
                "message_ids": ["m1"],
            }
        ],
    }


def test_build_context_limits_length_and_preserves_order():
    messages = [
        message("m1", datetime(2026, 8, 24, 9), "第一句"),
        message("m2", datetime(2026, 8, 24, 10), "第二句"),
    ]
    context = build_context(messages, max_chars=80)

    assert "第一句" in context
    assert "第二句" in context
    assert len(context) <= 80


def test_summarizer_returns_validated_result():
    ai = FakeAI(valid_response())
    summarizer = Summarizer(ai=ai, max_context_chars=1000)
    result = summarizer.summarize(
        group_id=123,
        group_name="测试群",
        window_start=datetime(2026, 8, 24, 0),
        window_end=datetime(2026, 8, 24, 22),
        messages=[message("m1", datetime(2026, 8, 24, 9), "https://example.com")],
    )

    assert isinstance(result, SummaryResult)
    assert result.response["main_topics"][0]["topic"] == "工具推荐"


def test_summarizer_rejects_wrong_group():
    response = valid_response()
    response["group_id"] = 999
    summarizer = Summarizer(ai=FakeAI(response), max_context_chars=1000)

    with pytest.raises(ValueError, match="group_id"):
        summarizer.summarize(
            group_id=123,
            group_name="测试群",
            window_start=datetime(2026, 8, 24, 0),
            window_end=datetime(2026, 8, 24, 22),
            messages=[],
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_summary.py -v`

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现 Summarizer**

```python
# qq_digest/summary.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from .models import NormalizedMessage
from .preprocessing import Preprocessor


class Topic(BaseModel):
    topic: str
    summary: str


class Resource(BaseModel):
    title: str
    link: str = ""


class CandidateOutput(BaseModel):
    type: str
    title: str
    link: str = ""
    content: str = ""
    reason: str
    message_ids: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in {"resource", "experience"}:
            raise ValueError("candidate.type 只支持 resource 或 experience")
        return value


class SummaryResponse(BaseModel):
    group_id: int
    main_topics: list[Topic]
    conclusions: list[str]
    resources: list[Resource]
    tasks: list[str]
    open_questions: list[str]
    candidates: list[CandidateOutput]


@dataclass(frozen=True)
class SummaryResult:
    response: SummaryResponse
    deterministic: dict[str, list[str]]
    context_chars: int


def build_context(messages: list[NormalizedMessage], max_chars: int) -> str:
    lines = []
    for message in messages:
        sender = message.sender_qq or "unknown"
        timestamp = message.timestamp.strftime("%H:%M")
        text = message.text.strip() or message.raw_digest.strip() or message.message_type
        lines.append(f"[{timestamp}|{sender}] {text}")
    context = "\n".join(lines)
    if len(context) <= max_chars:
        return context
    return context[-max_chars:]


class Summarizer:
    prompt = """你是可靠的QQ群消息摘要助手。请只输出JSON，不输出Markdown代码块。
JSON结构：
{
  "group_id": int,
  "main_topics": [{"topic": string, "summary": string}],
  "conclusions": [string],
  "resources": [{"title": string, "link": string}],
  "tasks": [string],
  "open_questions": [string],
  "candidates": [{"type": "resource"|"experience", "title": string, "link": string, "content": string, "reason": string, "message_ids": [string]}]
}
要求：
- 基于输入消息，不编造。
- 候选必须高价值、可复用、值得长期保存。
- 不要把闲聊作为候选。
"""

    def __init__(self, *, ai, max_context_chars: int):
        self.ai = ai
        self.max_context_chars = max_context_chars
        self.preprocessor = Preprocessor()

    def summarize(
        self,
        *,
        group_id: int,
        group_name: str,
        window_start: datetime,
        window_end: datetime,
        messages: list[NormalizedMessage],
    ) -> SummaryResult:
        context = build_context(messages, self.max_context_chars)
        cleaned = self.preprocessor.process([message.text for message in messages])
        deterministic = {
            "links": cleaned.links,
            "files": cleaned.files,
            "todos": cleaned.todos,
        }
        raw = self.ai.chat(
            [
                {"role": "system", "content": self.prompt},
                {
                    "role": "user",
                    "content": (
                        f"群ID: {group_id}\n群名: {group_name}\n"
                        f"时间窗: {window_start.isoformat()} 到 {window_end.isoformat()}\n"
                        f"确定性提取: {deterministic}\n\n消息：\n{context}"
                    ),
                },
            ]
        )
        try:
            response = SummaryResponse.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"AI响应结构无效: {exc}") from exc
        if response.group_id != group_id:
            raise ValueError("AI响应 group_id 不匹配")
        return SummaryResult(
            response=response,
            deterministic=deterministic,
            context_chars=len(context),
        )
```

- [ ] **Step 4: 运行摘要测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_summary.py -v`

Expected: 3 passed

- [ ] **Step 5: Commit**

```powershell
git add qq_digest/summary.py tests/test_summary.py
git commit -m "feat: add daily summary pipeline"
```

---

### Task 6: 报告与候选入库

**Files:**

- Create: `qq_digest/reports.py`
- Create: `qq_digest/candidates.py`
- Test: `tests/test_reports.py`
- Test: `tests/test_candidates.py`

- [ ] **Step 1: 写报告失败测试**

```python
# tests/test_reports.py
import json
from datetime import datetime
from pathlib import Path

from qq_digest.reports import ReportWriter, render_markdown


def test_render_markdown_contains_required_sections():
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-08-24",
        window="2026-08-24 00:00 到 22:00",
        topics=["工具推荐"],
        conclusions=["站点适合归档"],
        resources=["[站点](https://example.com)"],
        tasks=[],
        open_questions=[],
        deterministic={"links": ["https://example.com"], "files": [], "todos": []},
        quality_note="",
    )

    assert "# 测试群日报" in markdown
    assert "## 主要话题" in markdown
    assert "## 数据质量" not in markdown


def test_report_writer_creates_markdown_and_json(tmp_path):
    writer = ReportWriter(tmp_path)
    payload = {"group_id": 123, "group_name": "测试群", "main_topics": []}
    paths = writer.write(
        group_id=123,
        group_name="测试群",
        report_date="2026-08-24",
        markdown="# 测试",
        payload=payload,
    )

    assert paths.markdown.name == "2026-08-24__123.md"
    assert paths.json.name == "2026-08-24__123.json"
    assert json.loads(paths.json.read_text(encoding="utf-8")) == payload
```

```python
# tests/test_candidates.py
from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService


def test_candidate_lifecycle(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups(
        [
            type(
                "Group",
                (),
                {
                    "group_id": 123,
                    "name": "测试群",
                    "enabled": True,
                    "daily_summary": True,
                    "template": "concise",
                    "keywords": [],
                    "important_candidates": True,
                    "collection_window_days": 30,
                },
            )()
        ]
    )
    service = CandidateService(archive)
    candidate_id = service.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type="resource",
        title="站点",
        link="https://example.com",
        content="",
        reason="高质量教程",
        excerpt="看这个站点",
        message_ids=["m1"],
    )

    assert service.pending(group_id=123)[0].candidate_id == candidate_id
    service.confirm(candidate_id)
    assert service.get(candidate_id).status == "confirmed"
    service.update_status(candidate_id, "pending", ignore_reason="")
    service.ignore(candidate_id, "重复")
    assert service.get(candidate_id).ignore_reason == "重复"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_reports.py tests/test_candidates.py -v`

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现报告和候选服务**

```python
# qq_digest/reports.py
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
        self.output_dir = output_dir
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
```

```python
# qq_digest/candidates.py
from __future__ import annotations

import json
from datetime import datetime, timezone

from .archive import Archive
from .models import SummaryCandidate


class CandidateService:
    def __init__(self, archive: Archive):
        self.archive = archive

    def create(self, **kwargs) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.archive.transaction():
            cursor = self.archive.connection.execute(
                """
                INSERT INTO candidates(
                    group_id, message_ids, created_date, candidate_type, title,
                    link, content, reason, excerpt, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    kwargs["group_id"],
                    json.dumps(kwargs["message_ids"], ensure_ascii=False),
                    kwargs["created_date"],
                    kwargs["candidate_type"],
                    kwargs["title"],
                    kwargs.get("link", ""),
                    kwargs.get("content", ""),
                    kwargs["reason"],
                    kwargs.get("excerpt", ""),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def _row_to_candidate(self, row) -> SummaryCandidate:
        return SummaryCandidate(
            candidate_id=row["candidate_id"],
            group_id=row["group_id"],
            message_ids=json.loads(row["message_ids"]),
            created_date=row["created_date"],
            candidate_type=row["candidate_type"],
            title=row["title"],
            link=row["link"],
            content=row["content"],
            reason=row["reason"],
            excerpt=row["excerpt"],
            status=row["status"],
        )

    def pending(self, group_id: int | None = None) -> list[SummaryCandidate]:
        query = "SELECT * FROM candidates WHERE status='pending'"
        params: tuple = ()
        if group_id is not None:
            query += " AND group_id=?"
            params = (group_id,)
        query += " ORDER BY created_at DESC"
        return [self._row_to_candidate(row) for row in self.archive.connection.execute(query, params)]

    def get(self, candidate_id: int) -> SummaryCandidate:
        row = self.archive.connection.execute(
            "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"候选 {candidate_id} 不存在")
        return self._row_to_candidate(row)

    def update_status(self, candidate_id: int, status: str, ignore_reason: str = "") -> None:
        if status not in {"pending", "confirmed", "ignored", "later"}:
            raise ValueError("非法候选状态")
        now = datetime.now(timezone.utc).isoformat()
        with self.archive.transaction():
            cursor = self.archive.connection.execute(
                """
                UPDATE candidates SET status=?, ignore_reason=?, updated_at=?
                WHERE candidate_id=?
                """,
                (status, ignore_reason, now, candidate_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"候选 {candidate_id} 不存在")

    def confirm(self, candidate_id: int) -> None:
        self.update_status(candidate_id, "confirmed")

    def ignore(self, candidate_id: int, reason: str = "") -> None:
        self.update_status(candidate_id, "ignored", reason)
```

- [ ] **Step 4: 运行报告与候选测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_reports.py tests/test_candidates.py -v`

Expected: 4 passed

- [ ] **Step 5: Commit**

```powershell
git add qq_digest/reports.py qq_digest/candidates.py tests/test_reports.py tests/test_candidates.py
git commit -m "feat: add reports and review candidates"
```

---

### Task 7: Markdown 知识库幂等写入

**Files:**

- Create: `qq_digest/knowledge.py`
- Test: `tests/test_knowledge.py`

- [ ] **Step 1: 写知识库失败测试**

```python
# tests/test_knowledge.py
from pathlib import Path

from qq_digest.knowledge import KnowledgeWriter, KnowledgeItem, write_item


def item(item_id="c1"):
    return KnowledgeItem(
        item_id=item_id,
        date="2026-08-24",
        category="资源",
        source_group="测试群",
        title="站点",
        link="https://example.com",
        value="高质量教程",
        excerpt="看这个站点",
        report_path="reports/2026-08-24__123.md",
    )


def test_write_item_is_idempotent(tmp_path):
    path = tmp_path / "resources.md"
    write_item(path, item())
    write_item(path, item())

    content = path.read_text(encoding="utf-8")
    assert content.count("## 2026-08-24 | 资源") == 1
    assert "条目 ID：c1" in content


def test_knowledge_writer_routes_by_category(tmp_path):
    writer = KnowledgeWriter(tmp_path)
    writer.write(item(), "resource")
    assert (tmp_path / "resources.md").exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_knowledge.py -v`

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现 KnowledgeWriter**

```python
# qq_digest/knowledge.py
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
        self.directory = directory

    def write(self, item: KnowledgeItem, item_type: str) -> Path:
        filename = self.filenames.get(item_type)
        if filename is None:
            raise ValueError("item_type 只支持 resource 或 experience")
        path = self.directory / filename
        write_item(path, item)
        return path
```

- [ ] **Step 4: 运行知识库测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_knowledge.py -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```powershell
git add qq_digest/knowledge.py tests/test_knowledge.py
git commit -m "feat: add markdown knowledge base"
```

---

### Task 8: Web 登录与候选审核

**Files:**

- Create: `qq_digest/web/__init__.py`
- Create: `qq_digest/web/auth.py`
- Create: `qq_digest/web/app.py`
- Create: `qq_digest/web/templates/login.html`
- Create: `qq_digest/web/templates/candidates.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: 写 Web 失败测试**

```python
# tests/test_web.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher


@pytest.fixture
def web_client(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups(
        [
            type(
                "Group",
                (),
                {
                    "group_id": 123,
                    "name": "测试群",
                    "enabled": True,
                    "daily_summary": True,
                    "template": "concise",
                    "keywords": [],
                    "important_candidates": True,
                    "collection_window_days": 30,
                },
            )()
        ]
    )
    candidates = CandidateService(archive)
    candidate_id = candidates.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type="resource",
        title="站点",
        link="https://example.com",
        reason="高质量教程",
        excerpt="看这个站点",
        message_ids=["m1"],
    )
    app = create_app(
        archive=archive,
        candidates=candidates,
        knowledge=KnowledgeWriter(tmp_path / "knowledge"),
        password_hash=PasswordHasher.hash("password123"),
        session_secret="test-secret",
    )
    client = TestClient(app)
    return client, candidate_id, tmp_path / "knowledge"


def test_candidates_requires_login(web_client):
    client, _, _ = web_client
    response = client.get("/candidates", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_and_confirm_candidate(web_client):
    client, candidate_id, knowledge_dir = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)
    response = client.post(f"/candidates/{candidate_id}/confirm", follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/candidates").status_code == 200
    markdown = (knowledge_dir / "resources.md").read_text(encoding="utf-8")
    assert "站点" in markdown
    assert f"条目 ID：{candidate_id}" in markdown
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -v`

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现认证和路由**

```python
# qq_digest/web/auth.py
from __future__ import annotations

import hashlib
import hmac
import secrets
import time


class PasswordHasher:
    algorithm = "sha256"

    @classmethod
    def hash(cls, password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(8)
        digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return f"{cls.algorithm}${salt}${digest}"

    @classmethod
    def verify(cls, password: str, stored: str) -> bool:
        try:
            algorithm, salt, digest = stored.split("$", 2)
        except ValueError:
            return False
        if algorithm != cls.algorithm:
            return False
        actual = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual, digest)


class SessionCookie:
    def __init__(self, secret: str, ttl_seconds: int):
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(self) -> str:
        expires = str(int(time.time()) + self.ttl_seconds)
        signature = hmac.new(self.secret, expires.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def verify(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        expires, signature = value.split(".", 1)
        expected = hmac.new(self.secret, expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expires) >= int(time.time())
```

```python
# qq_digest/web/app.py
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..archive import Archive
from ..candidates import CandidateService
from ..knowledge import KnowledgeItem, KnowledgeWriter
from .auth import PasswordHasher, SessionCookie


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def require_login(request: Request):
    cookie = request.app.state.cookie
    if not cookie.verify(request.cookies.get("qq_digest_session")):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def create_app(
    *,
    archive: Archive,
    candidates: CandidateService,
    knowledge: KnowledgeWriter,
    password_hash: str,
    session_secret: str,
    session_hours: int = 12,
):
    from fastapi import FastAPI

    app = FastAPI(title="QQ Digest")
    cookie = SessionCookie(session_secret, session_hours * 3600)
    app.state.archive = archive
    app.state.candidates = candidates
    app.state.knowledge = knowledge
    app.state.password_hash = password_hash
    app.state.cookie = cookie

    @app.get("/login")
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {})

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if not PasswordHasher.verify(password, app.state.password_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "密码错误"}, status_code=401
            )
        response = RedirectResponse("/candidates", status_code=303)
        response.set_cookie("qq_digest_session", cookie.issue(), httponly=True)
        return response

    @app.get("/candidates")
    async def candidate_list(request: Request):
        if not cookie.verify(request.cookies.get("qq_digest_session")):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {"candidates": candidates.pending()},
        )

    @app.post("/candidates/{candidate_id}/confirm")
    async def confirm(request: Request, candidate_id: int):
        require_login(request)
        candidate = candidates.get(candidate_id)
        knowledge.write(
            KnowledgeItem(
                item_id=str(candidate_id),
                date=candidate.created_date,
                category="资源" if candidate.candidate_type == "resource" else "经验",
                source_group=str(candidate.group_id),
                title=candidate.title,
                link=candidate.link,
                value=candidate.reason,
                excerpt=candidate.excerpt,
            ),
            candidate.candidate_type,
        )
        candidates.confirm(candidate_id)
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/ignore")
    async def ignore(request: Request, candidate_id: int, reason: str = Form("")):
        require_login(request)
        candidates.ignore(candidate_id, reason)
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/later")
    async def later(request: Request, candidate_id: int):
        require_login(request)
        candidates.update_status(candidate_id, "later")
        return RedirectResponse("/candidates", status_code=303)

    return app
```

```html
<!-- qq_digest/web/templates/login.html -->
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>登录 - QQ Digest</title></head>
<body>
  <h1>QQ Digest</h1>
  {% if error %}<p>{{ error }}</p>{% endif %}
  <form method="post" action="/login">
    <label>密码 <input type="password" name="password" required></label>
    <button type="submit">登录</button>
  </form>
</body>
</html>
```

```html
<!-- qq_digest/web/templates/candidates.html -->
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>待审核 - QQ Digest</title></head>
<body>
  <h1>待审核重点信息</h1>
  {% for candidate in candidates %}
  <article>
    <h2>{{ candidate.title }}</h2>
    <p>{{ candidate.reason }}</p>
    {% if candidate.link %}<p><a href="{{ candidate.link }}">{{ candidate.link }}</a></p>{% endif %}
    <form method="post" action="/candidates/{{ candidate.candidate_id }}/confirm"><button>入库</button></form>
    <form method="post" action="/candidates/{{ candidate.candidate_id }}/ignore"><input name="reason" placeholder="忽略原因"><button>忽略</button></form>
    <form method="post" action="/candidates/{{ candidate.candidate_id }}/later"><button>稍后</button></form>
  </article>
  {% else %}
  <p>暂无待审核候选</p>
  {% endfor %}
</body>
</html>
```

```python
# qq_digest/web/__init__.py
from .app import create_app

__all__ = ["create_app"]
```

不要创建空的 `candidate_detail.html`；本任务只交付列表和操作，详情路由放入官方 QQ Bot 计划前的 Web 增强计划，避免留下不可用页面。

- [ ] **Step 4: 运行 Web 测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```powershell
git add qq_digest/web tests/test_web.py
git commit -m "feat: add authenticated review web ui"
```

---

### Task 9: Collector 接口、Fixture 与 CLI

**Files:**

- Create: `qq_digest/collector/__init__.py`
- Create: `qq_digest/collector/base.py`
- Create: `qq_digest/collector/fixture.py`
- Create: `qq_digest/cli.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: 写 CLI 失败测试**

```python
# tests/test_cli.py
from pathlib import Path

from typer.testing import CliRunner

from qq_digest.cli import app


runner = CliRunner()


def test_hash_password():
    result = runner.invoke(app, ["hash-password", "password123"])

    assert result.exit_code == 0
    assert result.stdout.count("$") == 2
```

```python
# tests/test_scheduler.py
from datetime import datetime

from qq_digest.scheduler import next_run, summary_window


def test_summary_window_today():
    now = datetime(2026, 8, 24, 22, 0)
    start, end = summary_window(now, "today")

    assert start == datetime(2026, 8, 24, 0, 0)
    assert end == now


def test_summary_window_previous_day():
    now = datetime(2026, 8, 24, 22, 0)
    start, end = summary_window(now, "previous_day")

    assert start == datetime(2026, 8, 23, 0, 0)
    assert end == datetime(2026, 8, 23, 23, 59, 59, 999999)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py tests/test_scheduler.py -v`

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现 collector 和基础 CLI**

```python
# qq_digest/collector/base.py
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from ..models import NormalizedMessage


class Collector(ABC):
    @abstractmethod
    def discover_groups(self) -> list[dict]:
        """返回 group_id、name、latest_message_at、message_count_30d。"""

    @abstractmethod
    def collect(
        self, group_id: int, start: datetime, end: datetime
    ) -> Iterable[NormalizedMessage]:
        """按时间窗读取消息。实现不得修改原始 QQ 数据。"""
```

```python
# qq_digest/collector/fixture.py
from datetime import datetime, timedelta

from ..models import NormalizedMessage
from .base import Collector


class FixtureCollector(Collector):
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 8, 24, 22)

    def discover_groups(self):
        return [
            {
                "group_id": 123,
                "name": "测试群",
                "latest_message_at": self.now,
                "message_count_30d": 1,
            }
        ]

    def collect(self, group_id, start, end):
        if group_id != 123:
            return
        timestamp = min(self.now, end)
        if start <= timestamp <= end:
            yield NormalizedMessage(
                msg_id=f"fixture-{group_id}",
                group_id=group_id,
                sender_qq=10001,
                timestamp=timestamp,
                text="https://example.com 是高质量教程站点",
                collected_at=self.now,
            )
```

```python
# qq_digest/collector/__init__.py
from .base import Collector
from .fixture import FixtureCollector

__all__ = ["Collector", "FixtureCollector"]
```

```python
# qq_digest/scheduler.py
from __future__ import annotations

from datetime import datetime, timedelta


def next_run(now: datetime, hour: int, minute: int = 0) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def summary_window(now: datetime, mode: str) -> tuple[datetime, datetime]:
    if mode == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    if mode == "previous_day":
        next_day = datetime.combine(now.date(), datetime.min.time())
        return next_day - timedelta(days=1), next_day - timedelta(microseconds=1)
    raise ValueError("mode 只支持 today 或 previous_day")
```

CLI 依赖加入 `pyproject.toml`：

```toml
"typer>=0.12",
```

```python
# qq_digest/cli.py
from __future__ import annotations

from pathlib import Path

import typer
import os
import uvicorn

from .config import load_config
from .archive import Archive
from .candidates import CandidateService
from .knowledge import KnowledgeWriter
from .web.app import create_app
from .web.auth import PasswordHasher


app = typer.Typer(help="QQ 群消息摘要工具")


@app.command("hash-password")
def hash_password(password: str) -> None:
    """生成 Web 登录密码散列。"""
    typer.echo(PasswordHasher.hash(password))


@app.command("doctor")
def doctor(config_path: Path = typer.Option(Path("config/config.yaml"))) -> None:
    """执行基础配置和依赖检查。"""
    try:
        config = load_config(config_path)
    except Exception as exc:
        typer.echo(f"[阻断] 配置检查失败: {exc}")
        raise typer.Exit(1)
    typer.echo("[通过] 配置可加载")
    typer.echo(f"[通过] 归档路径: {config.archive_path}")


@app.command("serve")
def serve(config_path: Path = typer.Option(Path("config/config.yaml"))) -> None:
    """启动 Web 审核界面。"""
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.upsert_groups(config.groups)
    app = create_app(
        archive=archive,
        candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(config.knowledge_dir),
        password_hash=config.security.web_password_hash,
        session_secret=os.environ.get(config.security.session_secret_env, ""),
        session_hours=config.security.session_hours,
    )
    if not app.state.cookie.secret:
        raise typer.Exit("QQ_DIGEST_SESSION_SECRET 未设置")
    uvicorn.run(app, host="0.0.0.0", port=8765)
```

- [ ] **Step 4: 运行 CLI 和调度测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py tests/test_scheduler.py -v`

Expected: 3 passed

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml qq_digest/collector qq_digest/cli.py qq_digest/scheduler.py tests
git commit -m "feat: add collector interface and cli foundation"
```

---

### Task 10: MVP 集成测试与文档

**Files:**

- Create: `tests/test_integration.py`
- Create: `README.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: 写集成失败测试**

```python
# tests/test_integration.py
from datetime import datetime

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.collector.fixture import FixtureCollector
from qq_digest.models import GroupConfig
from qq_digest.reports import ReportWriter, render_markdown
from qq_digest.summary import Summarizer


class FakeAI:
    def chat(self, messages):
        return {
            "group_id": 123,
            "main_topics": [{"topic": "资源分享", "summary": "分享了教程站点"}],
            "conclusions": [],
            "resources": [{"title": "站点", "link": "https://example.com"}],
            "tasks": [],
            "open_questions": [],
            "candidates": [
                {
                    "type": "resource",
                    "title": "站点",
                    "link": "https://example.com",
                    "reason": "高质量教程",
                    "message_ids": ["fixture-123"],
                }
            ],
        }


def test_collect_summary_report_candidate(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    group = GroupConfig(group_id=123, name="测试群")
    archive.upsert_groups([group])
    result = archive.ingest(FixtureCollector().collect(123, datetime(2026, 8, 24, 0), datetime(2026, 8, 24, 22)))
    messages = archive.messages_between(123, datetime(2026, 8, 24, 0), datetime(2026, 8, 24, 22))
    summary = Summarizer(ai=FakeAI(), max_context_chars=1000).summarize(
        group_id=123,
        group_name="测试群",
        window_start=datetime(2026, 8, 24, 0),
        window_end=datetime(2026, 8, 24, 22),
        messages=messages,
    )
    candidates = CandidateService(archive)
    candidate_id = candidates.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type=summary.response.candidates[0].type,
        title=summary.response.candidates[0].title,
        link=summary.response.candidates[0].link,
        content=summary.response.candidates[0].content,
        reason=summary.response.candidates[0].reason,
        excerpt=messages[0].text[:100],
        message_ids=summary.response.candidates[0].message_ids,
    )
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-08-24",
        window="2026-08-24 00:00 到 22:00",
        topics=[item.topic for item in summary.response.main_topics],
        conclusions=summary.response.conclusions,
        resources=[item.title for item in summary.response.resources],
        tasks=summary.response.tasks,
        open_questions=summary.response.open_questions,
        deterministic=summary.deterministic,
        quality_note="",
    )
    paths = ReportWriter(tmp_path / "reports").write(
        group_id=123,
        group_name="测试群",
        report_date="2026-08-24",
        markdown=markdown,
        payload=summary.response.model_dump(),
    )

    assert result.inserted == 1
    assert paths.markdown.exists()
    assert candidates.get(candidate_id).status == "pending"
```

- [ ] **Step 2: 运行集成测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_integration.py -v`

Expected: 先失败于缺失文件；实现完成后通过。

- [ ] **Step 3: 编写 README**

````markdown
# QQ Digest

个人 QQ 群消息摘要、重点信息审核和 Markdown 知识库工具。

## 当前阶段

MVP 提供本地归档、每日摘要流水线、Web 审核、Markdown 入库和基础 CLI。真实 QQNT 数据库适配与官方 QQ Bot 通知在后续阶段实现。

## 安装

```powershell
D:\CodexTools\python\Scripts\python.exe -m pip install -e ".[test]"
```

## 初始化

```powershell
Copy-Item config/config.example.yaml config/config.yaml
qq-digest hash-password "你的密码"
```

把输出的散列写入 `config.yaml` 的 `security.web_password_hash`，再设置：

```powershell
$env:QQ_DIGEST_SESSION_SECRET="随机长字符串"
$env:QQ_DIGEST_AI_API_KEY="你的API密钥"
```

## 检查

```powershell
qq-digest doctor
```

## 测试

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest
```

## 安全边界

- 不修改原始 QQ 数据库。
- API key 和 QQ Bot 密钥只来自环境变量。
- Web UI 面向 localhost/局域网，不暴露公网。
````

- [ ] **Step 4: 运行全部测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest -v`

Expected: 全部通过，无警告或警告均可解释。

- [ ] **Step 5: Commit**

```powershell
git add tests/test_integration.py README.md pyproject.toml
git commit -m "feat: complete local digest mvp"
```

---

## 后续计划

### QQNT Adapter

必须单独成计划，验收标准包括：

- 能定位 QQNT 数据库。
- 能创建只读快照。
- 能验证数据库结构。
- 能读取群消息。
- 密钥提取失败、结构变化和解析率低都进入明确状态。
- 原库哈希在采集前后一致。

### 官方 QQ Bot

必须单独成计划，验收标准包括：

- AppID/AppSecret 获取 token。
- 私信发送摘要。
- 白名单 OpenID 指令鉴权。
- 候选、入库、忽略、稍后、查看五类指令。
- 发送日志和重试。

### Web 群选择

在 QQNT Adapter 可以真实扫描群后实现：

- 展示群名称、群号、最近消息时间和最近 30 天消息量。
- 初始勾选后写入配置。
- 支持启用/禁用、每日摘要、模板、重点关键词、候选开关和采集窗口。
- 修改仍以 `config/config.yaml` 为最终权威来源。
