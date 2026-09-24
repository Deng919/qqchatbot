from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

from .models import GroupConfig


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
    base_url: str
    model: str
    api_key_env: str
    api_key_file: str = ""
    ui_api_key_file: str = ""
    json_mode: bool = False
    provider_priority: list[str] = Field(
        default_factory=lambda: ["chatgpt_bridge", "compatible"]
    )
    bridge_wrapper_path: str = (
        r"D:\CodexTools\gpttoimage-session\chat-current-account.ps1"
    )
    bridge_account_directory: str = r"D:\CodexTools\codexID"
    bridge_model: str = ""
    bridge_timeout_seconds: float = 180
    bridge_queue_timeout_seconds: int = 60
    timeout_seconds: float = 120
    max_context_chars: int = 60000
    max_retries: int = 3
    retry_base_seconds: float = 2.0


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


class Config(BaseModel):
    model_config = {"extra": "allow"}

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
        if self.ai.ui_api_key_file:
            from .ai.key_store import read_ui_api_key

            try:
                ui_key = read_ui_api_key(Path(self.ai.ui_api_key_file).expanduser())
            except (OSError, UnicodeError, ValueError) as exc:
                raise ConfigError("无法读取本地 DeepSeek API 密钥") from exc
            if ui_key:
                return ui_key
        value = os.environ.get(self.ai.api_key_env, "")
        if value:
            return value
        if self.ai.api_key_file:
            path = Path(self.ai.api_key_file).expanduser()
            if not path.is_absolute():
                path = self.data_dir / path
            try:
                value = path.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError) as exc:
                raise ConfigError("无法读取配置的 API 密钥文件") from exc
            if not value or any(char.isspace() for char in value):
                raise ConfigError("API 密钥文件必须包含一行非空密钥")
            return value
        return self._discover_codex_api_key()

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

    def _discover_codex_api_key(self) -> str:
        """Auto-discover API key from D:\\CodexTools\\codexID JSON files."""
        entry = self._discover_codex_entry()
        return entry.get("OPENAI_API_KEY", "")

    def _discover_codex_entry(self) -> dict:
        """Find the first valid API entry from codexID directory."""
        codex_id_dir = Path(r"D:\CodexTools\codexID")
        if not codex_id_dir.is_dir():
            raise ConfigError(
                f"环境变量 {self.ai.api_key_env} 未设置，且 {codex_id_dir} 不存在"
            )
        import json
        for json_file in codex_id_dir.glob("*.json"):
            try:
                entries = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(entries, dict):
                    entries = [entries]
                for entry in entries:
                    if entry.get("OPENAI_API_KEY") and entry.get("api_base_url"):
                        return entry
            except (json.JSONDecodeError, OSError):
                continue
        raise ConfigError(
            f"环境变量 {self.ai.api_key_env} 未设置，且 {codex_id_dir} 中无有效 API key"
        )

    def resolve_base_url(self) -> str:
        """Return configured base_url, or auto-discover from codexID."""
        url = self.ai.base_url.strip()
        if url and url not in ("auto", "https://api.example.com/v1"):
            return url
        entry = self._discover_codex_entry()
        return entry.get("api_base_url", "")


def load_config(path: Path) -> Config:
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
    allowed_providers = {"chatgpt_bridge", "compatible"}
    if not config.ai.provider_priority or any(
        provider not in allowed_providers for provider in config.ai.provider_priority
    ):
        raise ConfigError(
            "ai.provider_priority 只支持 chatgpt_bridge 和 compatible"
        )
    if len(config.ai.provider_priority) != len(set(config.ai.provider_priority)):
        raise ConfigError("ai.provider_priority 存在重复")
    if config.ai.ui_api_key_file and not Path(config.ai.ui_api_key_file).is_absolute():
        raise ConfigError("ai.ui_api_key_file 必须是绝对路径")
    if not Path(config.ai.bridge_wrapper_path).is_absolute():
        raise ConfigError("ai.bridge_wrapper_path 必须是绝对路径")
    if not Path(config.ai.bridge_account_directory).is_absolute():
        raise ConfigError("ai.bridge_account_directory 必须是绝对路径")
    if config.ai.bridge_timeout_seconds < 1:
        raise ConfigError("ai.bridge_timeout_seconds 必须至少为 1")
    if config.ai.bridge_queue_timeout_seconds < 1:
        raise ConfigError("ai.bridge_queue_timeout_seconds 必须至少为 1")
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

    config.archive_path.parent.mkdir(parents=True, exist_ok=True)
    config.report_dir.mkdir(parents=True, exist_ok=True)
    config.knowledge_dir.mkdir(parents=True, exist_ok=True)
    for knowledge_path in config.resolve_knowledge_paths().values():
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    return config
