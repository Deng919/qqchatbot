from pathlib import Path
from types import SimpleNamespace

import pytest

from qq_digest.ai import factory
from qq_digest.config import AIConfig, Config, ConfigError, SecurityConfig


def config(tmp_path, **ai_changes):
    ai = AIConfig(
        base_url="https://api.example.com/v1",
        model="legacy-model",
        api_key_env="TEST_KEY",
        **ai_changes,
    )
    return Config(data_dir=tmp_path, archive_path=tmp_path/'a.sqlite', report_dir=tmp_path/'r',
                  knowledge_dir=tmp_path/'k', work_dir=tmp_path/'w', log_dir=tmp_path/'l',
                  security=SecurityConfig(web_password_hash='x'*32), ai=ai)


def test_legacy_ai_config_has_no_profiles_and_keeps_connection_resolution(tmp_path):
    cfg = config(tmp_path)
    assert cfg.ai.connection_for('summary') is None
    assert cfg.ai.provider_for('summary') == cfg.ai.provider_priority[0]


def test_profile_is_selected_by_purpose(tmp_path):
    cfg = config(tmp_path, connections=[{
        'id': 'local', 'name': '本地兼容接口', 'provider': 'compatible',
        'base_url': 'http://127.0.0.1:9000/v1', 'model': 'local-chat',
        'api_key_env': 'LOCAL_KEY', 'supports_vision': True,
    }], purpose_connections={'summary': 'local', 'vision': 'local'})
    profile = cfg.ai.connection_for('summary')
    assert profile.id == 'local'
    assert cfg.ai.provider_for('vision') == 'compatible'
    assert cfg.ai.connection_for('archive_qa') is None


def test_unknown_profile_and_invalid_url_rejected(tmp_path):
    with pytest.raises(Exception):
        config(tmp_path, connections=[{
            'id': 'bad', 'name': 'bad', 'provider': 'compatible',
            'base_url': 'https://user:pass@example.com/v1', 'model': 'x',
        }])
    with pytest.raises(Exception):
        config(tmp_path, purpose_connections={'summary': 'missing'})


def test_factory_passes_selected_profile_to_compatible_builder(tmp_path, monkeypatch):
    cfg = config(tmp_path, connections=[{
        'id': 'secondary', 'name': '第二接口', 'provider': 'compatible',
        'base_url': 'https://secondary.example/v1', 'model': 'secondary-model',
        'api_key_env': 'SECONDARY_KEY',
    }], purpose_connections={'report_qa': 'secondary'})
    seen = {}
    class Client:
        def close(self): pass
    def build(config_value, name, purpose='summary', connection=None):
        seen.update(name=name, purpose=purpose, connection=connection)
        return Client()
    monkeypatch.setattr(factory, '_build_provider', build)
    client = factory.build_ai_client(cfg, purpose='report_qa')
    assert isinstance(client, Client)
    assert seen['name'] == 'compatible'
    assert seen['purpose'] == 'report_qa'
    assert seen['connection'].id == 'secondary'


def test_vision_requires_vision_capable_profile(tmp_path):
    with pytest.raises(Exception):
        config(tmp_path, connections=[{
            'id': 'text', 'name': '文字', 'provider': 'compatible',
            'base_url': 'https://text.example/v1', 'model': 'text-model',
        }], purpose_connections={'vision': 'text'})
