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


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765


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
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    ai: AIConfig
    groups: list[GroupConfig] = Field(default_factory=list)

    def resolve_api_key(self) -> str:
        value = os.environ.get(self.ai.api_key_env, "")
        if not value:
            raise ConfigError(f"环境变量 {self.ai.api_key_env} 未设置")
        return value


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
    try:
        ZoneInfo(config.summary.timezone)
    except Exception as exc:
        raise ConfigError(f"summary.timezone 无效: {exc}") from exc

    config.archive_path.parent.mkdir(parents=True, exist_ok=True)
    config.report_dir.mkdir(parents=True, exist_ok=True)
    config.knowledge_dir.mkdir(parents=True, exist_ok=True)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    return config
