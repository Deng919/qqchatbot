from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import GroupConfig
from .distribution import validate_ai_base_url


class ConfigError(ValueError):
    pass


class SummaryConfig(BaseModel):
    hour: int = 22
    minute: int = 0
    window_mode: str = "today"
    timezone: str = "Asia/Shanghai"
    max_attempts: int = 3
    retry_interval_minutes: int = 15


class AIConfig(BaseModel):
    # Legacy routing/provider fields are deliberately ignored when loading old YAML.
    base_url: str = "http://127.0.0.1:62406/v1"
    model: str = "gpt-6.1-sol"
    api_key_env: str = "COCKPIT_API_KEY"
    api_key_file: str = ""
    ui_api_key_file: str = ""
    json_mode: bool = False
    timeout_seconds: float = 120
    max_context_chars: int = 60000
    max_retries: int = 3
    retry_base_seconds: float = 2.0

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_endpoint(cls, value):
        # The old auto sentinel must never discover another account or provider.
        if isinstance(value, dict) and isinstance(value.get('base_url'), str):
            url = value['base_url'].strip()
            return {**value, 'base_url': 'http://127.0.0.1:62406/v1' if url in {'', 'auto'} else url}
        return value

    @model_validator(mode="after")
    def validate_connection(self):
        from .distribution import validate_ai_base_url
        validate_ai_base_url(self.base_url)
        if not self.model.strip() or len(self.model) > 200:
            raise ValueError("AI 模型名称不能为空且最多 200 个字符")
        if self.timeout_seconds < 1 or self.max_context_chars < 1:
            raise ValueError("AI 超时和上下文长度必须为正数")
        return self


class SecurityConfig(BaseModel):
    web_password_hash: str
    session_secret_env: str = "QQ_DIGEST_SESSION_SECRET"
    session_secret_file: str = ""
    session_hours: int = 12


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class CollectionConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = 10
    overlap_minutes: int = 60
    startup_delay_seconds: int = 15


class KnowledgeConfig(BaseModel):
    resource_path: str = "knowledge/resources.md"
    experience_path: str = "knowledge/experiences.md"


class NTQQConfig(BaseModel):
    enabled: bool = False
    qq_number: int = 0
    db_dir: str = ""
    timezone: str = "Asia/Shanghai"





class QQBotConfig(BaseModel):
    """QQ Bot 配置段。"""
    app_id: str = ""
    app_secret_env: str = "QQ_BOT_APP_SECRET"
    allowed_openids: list[str] = Field(default_factory=list)
    enabled: bool = False
    api_base: str = "https://api.sgroup.qq.com"
    websocket_url: str = "wss://api.sgroup.qq.com/websockets"
    max_retries: int = 3
    retry_base_seconds: int = 60


def retired_ai_key_files(raw: dict) -> dict[str, str]:
    """Retain file identities for cleanup/backup only; never retain executable routing."""
    saved = raw.get('retired_ai_key_files', {})
    paths = [value for value in saved.values() if isinstance(value, str) and value] if isinstance(saved, dict) else []
    ai = raw.get('ai', {})
    connections = ai.get('connections', []) if isinstance(ai, dict) else []
    if isinstance(connections, list):
        for connection in connections:
            if isinstance(connection, dict):
                paths.extend(value for field in ('api_key_file', 'ui_api_key_file')
                             if isinstance(value := connection.get(field), str) and value)
    return {str(index): value for index, value in enumerate(sorted(set(paths)))}


class Config(BaseModel):
    model_config = {"extra": "allow"}

    # Migration safety metadata: only credential file paths, absent from AI/UI routing.
    retired_ai_key_files: dict[str, str] = Field(default_factory=dict, repr=False)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_credentials(cls, value):
        if isinstance(value, dict):
            return {**value, 'retired_ai_key_files': retired_ai_key_files(value)}
        return value

    data_dir: Path
    archive_path: Path
    report_dir: Path
    knowledge_dir: Path
    work_dir: Path
    log_dir: Path
    security: SecurityConfig
    web: WebConfig = Field(default_factory=WebConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    ai: AIConfig
    ntqq: NTQQConfig = Field(default_factory=NTQQConfig)
    qq_bot: QQBotConfig = Field(default_factory=QQBotConfig)
    groups: list[GroupConfig] = Field(default_factory=list)

    def resolve_knowledge_paths(self) -> dict[str, Path]:
        def resolve(value: str) -> Path:
            path = Path(value).expanduser()
            return path if path.is_absolute() else self.data_dir / path

        return {
            "resource": resolve(self.knowledge.resource_path),
            "experience": resolve(self.knowledge.experience_path),
        }

    def resolve_api_key(self) -> str:
        source = self.ai
        if source.ui_api_key_file:
            from .ai.key_store import read_ui_api_key

            try:
                ui_key = self._read_key_file_with_retry(
                    lambda: read_ui_api_key(Path(source.ui_api_key_file).expanduser())
                )
            except (OSError, UnicodeError, ValueError) as exc:
                label = "Cockpit"
                raise ConfigError(f"无法读取{label} API 密钥") from exc
            if ui_key:
                return ui_key
        value = os.environ.get(source.api_key_env, "")
        if value:
            return value
        if source.api_key_file:
            path = Path(source.api_key_file).expanduser()
            if not path.is_absolute():
                path = self.data_dir / path
            try:
                value = self._read_key_file_with_retry(
                    lambda: path.read_text(encoding="utf-8-sig")
                ).strip()
            except (OSError, UnicodeError) as exc:
                raise ConfigError("无法读取配置的 API 密钥文件") from exc
            if not value or any(char.isspace() for char in value):
                raise ConfigError("API 密钥文件必须包含一行非空密钥")
            return value
        raise ConfigError("请配置 Cockpit API 密钥")

    @staticmethod
    def _read_key_file_with_retry(reader):
        import time

        for attempt in range(3):
            try:
                return reader()
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def resolve_session_secret(self) -> str:
        value = os.environ.get(self.security.session_secret_env, "")
        if value:
            return self._validate_session_secret(value)
        if not self.security.session_secret_file:
            return ""
        path = Path(self.security.session_secret_file).expanduser()
        if not path.is_absolute():
            path = self.data_dir / path
        try:
            value = path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            raise ConfigError("无法读取配置的 Session secret 文件") from exc
        return self._validate_session_secret(value)

    @staticmethod
    def _validate_session_secret(value: str) -> str:
        if len(value) < 32 or any(char.isspace() for char in value):
            raise ConfigError("Session secret 必须至少 32 个字符且不能包含空白")
        return value

    def resolve_base_url(self) -> str:
        url = self.ai.base_url.strip()
        validate_ai_base_url(url)
        return url


def load_config(path: Path, *, create_dirs: bool = True) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"无法读取配置: {exc}") from exc

    path = Path(path)
    if path.parent.name == "config":
        data_dir = path.parent.parent.resolve()
    else:
        data_dir = path.parent.resolve()
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

    if not 0 <= config.summary.hour <= 23:
        raise ConfigError("summary.hour 必须在 0 到 23 之间")
    if not 0 <= config.summary.minute <= 59:
        raise ConfigError("summary.minute 必须在 0 到 59 之间")
    if config.summary.window_mode not in {"today", "previous_day"}:
        raise ConfigError("summary.window_mode 只支持 today 或 previous_day")
    if config.summary.max_attempts < 1:
        raise ConfigError("summary.max_attempts 必须至少为 1")
    if config.summary.retry_interval_minutes < 1:
        raise ConfigError("summary.retry_interval_minutes 必须至少为 1")
    if config.ai.max_retries < 1:
        raise ConfigError("ai.max_retries 必须至少为 1")
    if config.ai.retry_base_seconds < 0:
        raise ConfigError("ai.retry_base_seconds 不能小于 0")
    if config.ai.ui_api_key_file and not Path(config.ai.ui_api_key_file).is_absolute():
        raise ConfigError("ai.ui_api_key_file 必须是绝对路径")
    if config.qq_bot.max_retries < 1:
        raise ConfigError("qq_bot.max_retries 必须至少为 1")
    if config.qq_bot.retry_base_seconds < 1:
        raise ConfigError("qq_bot.retry_base_seconds 必须至少为 1")
    if not 1 <= config.collection.interval_minutes <= 1440:
        raise ConfigError("collection.interval_minutes 必须在 1 到 1440 之间")
    if not 0 <= config.collection.overlap_minutes <= 1440:
        raise ConfigError("collection.overlap_minutes 必须在 0 到 1440 之间")
    if not 0 <= config.collection.startup_delay_seconds <= 3600:
        raise ConfigError("collection.startup_delay_seconds 必须在 0 到 3600 之间")
    try:
        ZoneInfo(config.summary.timezone)
    except Exception as exc:
        raise ConfigError(f"summary.timezone 无效: {exc}") from exc

    if create_dirs:
        config.archive_path.parent.mkdir(parents=True, exist_ok=True)
        config.report_dir.mkdir(parents=True, exist_ok=True)
        config.knowledge_dir.mkdir(parents=True, exist_ok=True)
        for knowledge_path in config.resolve_knowledge_paths().values():
            knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        config.work_dir.mkdir(parents=True, exist_ok=True)
        config.log_dir.mkdir(parents=True, exist_ok=True)
    return config
