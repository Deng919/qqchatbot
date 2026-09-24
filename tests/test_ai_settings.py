from pathlib import Path

import pytest

from qq_digest.ai.key_store import read_ui_api_key, save_ui_api_key, ui_api_key_exists
from qq_digest.config import AIConfig, Config, SecurityConfig


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path,
        archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports",
        knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="x" * 32),
        ai=AIConfig(
            base_url="https://api.example.com",
            model="deepseek-flash",
            api_key_env="TEST_DEEPSEEK_API_KEY",
            ui_api_key_file=str(tmp_path / "secrets" / "deepseek-api-key.txt"),
        ),
    )


def test_ui_key_file_takes_precedence_and_can_be_replaced(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    path = Path(config.ai.ui_api_key_file)
    monkeypatch.setenv(config.ai.api_key_env, "env-example-key")

    assert config.resolve_api_key() == "env-example-key"
    assert not ui_api_key_exists(path)
    save_ui_api_key(path, "first-example-key")
    assert ui_api_key_exists(path)
    assert read_ui_api_key(path) == "first-example-key"
    assert config.resolve_api_key() == "first-example-key"

    save_ui_api_key(path, "second-example-key")
    assert path.read_text(encoding="utf-8") == "second-example-key\n"
    assert config.resolve_api_key() == "second-example-key"


@pytest.mark.parametrize("value", ["", " ", "a b", "a\nb", "a\tb", "x" * 4097])
def test_ui_key_rejects_invalid_values_without_creating_file(tmp_path, value):
    path = tmp_path / "secrets" / "deepseek-api-key.txt"

    with pytest.raises(ValueError):
        save_ui_api_key(path, value)

    assert not path.exists()


def test_failed_key_replacement_keeps_previous_value(tmp_path, monkeypatch):
    path = tmp_path / "secrets" / "deepseek-api-key.txt"
    save_ui_api_key(path, "first-example-key")

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr("qq_digest.ai.key_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        save_ui_api_key(path, "second-example-key")

    assert read_ui_api_key(path) == "first-example-key"
    assert sorted(path.parent.iterdir()) == [path]


def test_ui_key_reader_rejects_multiline_file(tmp_path):
    path = tmp_path / "deepseek-api-key.txt"
    path.write_text("example-key\n\n", encoding="utf-8")

    with pytest.raises(ValueError):
        read_ui_api_key(path)
