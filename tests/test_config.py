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

    assert isinstance(config, Config)
    assert config.data_dir == tmp_path
    assert config.archive_path == tmp_path / "archive" / "archive.sqlite"
    assert config.report_dir == tmp_path / "reports"
    assert config.knowledge_dir == tmp_path / "knowledge"
    assert config.groups[0].name == "测试群"
    assert config.summary.hour == 22
    assert config.summary.window_mode == "today"
    assert config.web.host == "0.0.0.0"
    assert config.web.port == 8765


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


def test_load_config_rejects_invalid_summary_settings(tmp_path):
    raw = base_config(tmp_path)
    raw["summary"] = {"hour": 24, "window_mode": "invalid", "timezone": "Invalid/Zone"}
    path = write_config(tmp_path, raw)

    with pytest.raises(ConfigError, match="summary.hour"):
        load_config(path)

    raw["summary"]["hour"] = 22
    path = write_config(tmp_path, raw)
    with pytest.raises(ConfigError, match="window_mode"):
        load_config(path)

    raw["summary"]["window_mode"] = "today"
    path = write_config(tmp_path, raw)
    with pytest.raises(ConfigError, match="timezone"):
        load_config(path)
