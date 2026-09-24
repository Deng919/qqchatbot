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


def test_example_uses_direct_deepseek_v41_flash():
    path = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"
    example = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert example["ai"]["provider_priority"] == ["compatible"]
    assert example["ai"]["base_url"] == "https://api.deepseek.com"
    assert example["ai"]["model"] == "deepseek-flash"
    assert example["ai"]["json_mode"] is True
    assert example["ai"]["api_key_env"] == "DEEPSEEK_API_KEY"


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
    assert config.groups[0].template == "concise"
    assert config.summary.hour == 22
    assert config.summary.window_mode == "today"
    assert config.summary.max_attempts == 3
    assert config.summary.retry_interval_minutes == 15
    assert config.web.host == "127.0.0.1"
    assert config.collection.enabled is True
    assert config.collection.interval_minutes == 10
    assert config.collection.overlap_minutes == 60
    assert config.web.port == 8765
    assert config.ai.provider_priority == ["chatgpt_bridge", "compatible"]
    assert config.ai.bridge_account_directory == r"D:\CodexTools\codexID"


def test_load_config_rejects_invalid_retry_settings(tmp_path):
    raw = base_config(tmp_path)
    raw["summary"] = {"max_attempts": 0, "retry_interval_minutes": 0}
    raw["ai"]["max_retries"] = 0

    with pytest.raises(ConfigError, match="summary.max_attempts"):
        load_config(write_config(tmp_path, raw))

    raw["summary"]["max_attempts"] = 3
    with pytest.raises(ConfigError, match="summary.retry_interval_minutes"):
        load_config(write_config(tmp_path, raw))

    raw["summary"]["retry_interval_minutes"] = 15
    with pytest.raises(ConfigError, match="ai.max_retries"):
        load_config(write_config(tmp_path, raw))


def test_load_config_rejects_invalid_ai_provider_priority(tmp_path):
    raw = base_config(tmp_path)
    raw["ai"]["provider_priority"] = ["chatgpt_bridge", "unknown"]

    with pytest.raises(ConfigError, match="provider_priority"):
        load_config(write_config(tmp_path, raw))

    raw["ai"]["provider_priority"] = ["compatible", "compatible"]
    with pytest.raises(ConfigError, match="重复"):
        load_config(write_config(tmp_path, raw))


def test_load_config_rejects_relative_bridge_paths(tmp_path):
    raw = base_config(tmp_path)
    raw["ai"]["bridge_wrapper_path"] = "relative/wrapper.ps1"

    with pytest.raises(ConfigError, match="bridge_wrapper_path"):
        load_config(write_config(tmp_path, raw))


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


def test_knowledge_paths_default_to_existing_files(tmp_path):
    config = load_config(write_config(tmp_path, base_config(tmp_path)))

    paths = config.resolve_knowledge_paths()

    assert paths["resource"] == tmp_path / "knowledge" / "resources.md"
    assert paths["experience"] == tmp_path / "knowledge" / "experiences.md"


def test_knowledge_paths_support_relative_and_absolute_targets(tmp_path):
    raw = base_config(tmp_path)
    absolute = tmp_path / "outside" / "经验库.md"
    raw["knowledge"] = {
        "resource_path": "notes/资源.md",
        "experience_path": str(absolute),
    }

    config = load_config(write_config(tmp_path, raw))
    paths = config.resolve_knowledge_paths()

    assert paths["resource"] == tmp_path / "notes" / "资源.md"
    assert paths["experience"] == absolute
    assert paths["resource"].parent.exists()
    assert paths["experience"].parent.exists()


def test_session_secret_prefers_environment_over_file(tmp_path, monkeypatch):
    raw = base_config(tmp_path)
    secret_file = tmp_path / "session-secret.txt"
    secret_file.write_text("f" * 48, encoding="utf-8")
    raw["security"]["session_secret_file"] = str(secret_file)
    config = load_config(write_config(tmp_path, raw))
    monkeypatch.setenv("QQ_DIGEST_SESSION_SECRET", "e" * 48)

    assert config.resolve_session_secret() == "e" * 48


def test_session_secret_falls_back_to_configured_file(tmp_path, monkeypatch):
    raw = base_config(tmp_path)
    secret_file = tmp_path / "session-secret.txt"
    secret_file.write_text("s" * 48 + "\n", encoding="utf-8")
    raw["security"]["session_secret_file"] = str(secret_file)
    config = load_config(write_config(tmp_path, raw))
    monkeypatch.delenv("QQ_DIGEST_SESSION_SECRET", raising=False)

    assert config.resolve_session_secret() == "s" * 48


@pytest.mark.parametrize("value", ["", "short", "contains whitespace " + "x" * 32])
def test_session_secret_rejects_invalid_file_content(tmp_path, monkeypatch, value):
    raw = base_config(tmp_path)
    secret_file = tmp_path / "session-secret.txt"
    secret_file.write_text(value, encoding="utf-8")
    raw["security"]["session_secret_file"] = str(secret_file)
    config = load_config(write_config(tmp_path, raw))
    monkeypatch.delenv("QQ_DIGEST_SESSION_SECRET", raising=False)

    with pytest.raises(ConfigError, match="Session secret"):
        config.resolve_session_secret()
