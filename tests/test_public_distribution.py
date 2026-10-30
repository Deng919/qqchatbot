import pytest
from qq_digest import distribution
from qq_digest.config import ConfigError, load_config
from qq_digest.ai.factory import build_ai_client
from tests.test_first_use import setup_env

@pytest.mark.parametrize('url',['http://127.0.0.1:62406/v1','http://localhost:62406/v1','https://cockpit.example/v1'])
def test_valid_cockpit_endpoint(url):
    distribution.validate_ai_base_url(url)

@pytest.mark.parametrize('url',['auto','ftp://example.com','https://user:secret@example.com','https://example.com?key=secret','https://example.com#x','http://localhost:0/v1','https://example.com:99999','https://example.com/\npath'])
def test_reject_invalid_cockpit_endpoint(url):
    with pytest.raises(ConfigError):distribution.validate_ai_base_url(url)

@pytest.mark.parametrize('public',[False,True])
def test_all_editions_use_same_connection_without_account_discovery(setup_env,monkeypatch,public):
    path,cfg,_,_=setup_env
    monkeypatch.setattr(distribution,'is_public_distribution',lambda:public)
    monkeypatch.delenv('TEST_KEY',raising=False)
    with pytest.raises(ConfigError,match='Cockpit'):cfg.resolve_api_key()
    monkeypatch.setenv('TEST_KEY','synthetic-cockpit-key')
    loaded=load_config(path,create_dirs=False)
    client=build_ai_client(loaded)
    assert client.base_url=='https://example.com/v1' and client.model=='sample'
    client.close()

def test_compiled_public_flag_remains_build_metadata(monkeypatch,tmp_path):
    import sys
    monkeypatch.setattr(distribution,'MARKER_PATH',tmp_path/'missing.json')
    monkeypatch.setattr(sys,'_qq_digest_public_build',True,raising=False)
    assert distribution.is_public_distribution()
