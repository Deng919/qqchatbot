import pytest
from types import SimpleNamespace
from qq_digest import distribution
from qq_digest.config import ConfigError

@pytest.mark.parametrize('url', ['https://api.deepseek.com', 'https://api.deepseek.com/', 'https://api.deepseek.com/v1', 'https://api.deepseek.com:443/v1/'])
def test_official_endpoint(url):
    distribution.validate_public_ai(['compatible'], url)

@pytest.mark.parametrize('url', ['auto','http://api.deepseek.com','https://example.com','https://api.deepseek.com:444','https://x@api.deepseek.com','https://api.deepseek.com/v2','https://api.deepseek.com?x=1','https://api.deepseek.com#x'])
def test_reject_other_endpoints(url):
    with pytest.raises(ConfigError): distribution.validate_public_ai(['compatible'], url)

@pytest.mark.parametrize('providers', [['chatgpt_bridge'], ['compatible','chatgpt_bridge'], []])
def test_reject_bridge(providers):
    with pytest.raises(ConfigError): distribution.validate_public_ai(providers, 'https://api.deepseek.com')
from tests.test_first_use import setup_env, service
from qq_digest.config import load_config
from qq_digest.ai.factory import build_ai_client

@pytest.fixture
def public(monkeypatch, tmp_path):
    marker = tmp_path / 'distribution.json'
    marker.write_text('{"edition":"deepseek-only","version":"0.2.0-beta.1"}')
    monkeypatch.setattr(distribution, 'MARKER_PATH', marker)
    assert distribution.is_public_distribution()


def test_config_loading_and_factory_reject_before_key_lookup(setup_env, public, monkeypatch):
    path, cfg, _, _ = setup_env
    with pytest.raises(ConfigError): load_config(path, create_dirs=False)
    monkeypatch.setattr(type(cfg), 'resolve_api_key', lambda _: pytest.fail('key resolution ran'))
    with pytest.raises(ConfigError): build_ai_client(cfg)


def test_public_never_discovers_local_accounts(setup_env, public, monkeypatch):
    _, cfg, _, _ = setup_env
    monkeypatch.delenv('TEST_KEY', raising=False)
    monkeypatch.setattr(type(cfg), '_discover_codex_entry', lambda _: pytest.fail('account discovery ran'))
    with pytest.raises(ConfigError): cfg.resolve_api_key()
    cfg.ai.base_url = 'auto'
    with pytest.raises(ConfigError): cfg.resolve_base_url()
    cfg.ai.base_url = 'https://api.deepseek.com'
    assert cfg.resolve_base_url() == 'https://api.deepseek.com'


def test_public_first_use_rejects_before_mutation(setup_env, public):
    path, cfg, _, _ = setup_env
    svc = service(setup_env)
    before = path.read_bytes()
    with pytest.raises(ConfigError):
        svc.set_ai(svc.store.revision, 'chatgpt_bridge', 'https://api.deepseek.com', '', 'synthetic-key')
    with pytest.raises(ConfigError):
        svc.set_ai(svc.store.revision, 'compatible', 'https://example.com', 'sample', 'synthetic-key')
    assert path.read_bytes() == before
    assert not (cfg.data_dir / 'config' / 'secrets').exists()
    assert 'bridge_model' not in svc.snapshot()['ai']
    assert 'bridge_available' not in svc.snapshot()['ai']


def test_public_templates_have_no_bridge_or_fallback(public):
    from qq_digest.web.app import templates
    for name in ('first_use.html', 'first_use_script.html', 'ai_connection_settings.html'):
        rendered = templates.env.get_template(name).render(features={}, request=SimpleNamespace(url=SimpleNamespace(path='/setup')))
        assert 'chatgpt_bridge' not in rendered
        assert 'bridge_model' not in rendered
        assert '桥接' not in rendered
        assert 'summary-fallback' not in rendered


def test_public_factory_does_not_import_bridge(tmp_path):
    import subprocess, sys
    script = '''
import sys
from types import SimpleNamespace
import qq_digest.distribution as policy
policy.MARKER_PATH = __import__('pathlib').Path(sys.argv[1])
from qq_digest.ai.factory import build_ai_client
cfg = SimpleNamespace(ai=SimpleNamespace(provider_priority=['compatible'],base_url='https://api.deepseek.com',model='deepseek-chat',json_mode=True,timeout_seconds=1,max_retries=1,retry_base_seconds=0),resolve_base_url=lambda:'https://api.deepseek.com',resolve_api_key=lambda:'synthetic-key')
client=build_ai_client(cfg)
assert 'qq_digest.ai.bridge' not in sys.modules
client.close()
'''
    marker = tmp_path / 'distribution.json'
    marker.write_text('{}')
    subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(marker)], check=True)

def test_public_http_pages_and_api_omit_bridge(setup_env, public):
    from fastapi.testclient import TestClient
    from qq_digest.web.app import create_app
    from qq_digest.web.auth import PasswordHasher
    from qq_digest.candidates import CandidateService
    from qq_digest.knowledge import KnowledgeWriter
    path, cfg, archive, _ = setup_env
    cfg.ai.provider_priority = ['compatible']
    cfg.ai.base_url = 'https://api.deepseek.com'
    client = TestClient(create_app(archive=archive, candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(cfg.resolve_knowledge_paths()), password_hash=PasswordHasher.hash('password123'),
        session_secret='synthetic-session-secret', config=cfg, config_path=path))
    client.post('/login', data={'password':'password123'})
    for url in ('/setup', '/ai-settings'):
        response = client.get(url)
        assert response.status_code == 200
        assert 'chatgpt_bridge' not in response.text
        assert '桥接' not in response.text
        assert 'summary-fallback' not in response.text
    response = client.get('/api/ai-settings')
    assert response.status_code == 200
    assert 'bridge_model' not in response.json()
    assert client.get('/api/setup').json()['ai']['provider'] == 'compatible'


def test_compiled_public_flag_cannot_be_disabled_by_missing_marker(tmp_path):
    import subprocess, sys
    script = """
import sys
from pathlib import Path
sys._qq_digest_public_build = True
from qq_digest import distribution
from qq_digest.config import ConfigError
from qq_digest.ai.factory import build_ai_client
from types import SimpleNamespace
distribution.MARKER_PATH = Path(sys.argv[1])
assert not distribution.MARKER_PATH.exists()
assert distribution.is_public_distribution()
for providers, url in [(['chatgpt_bridge'], 'https://api.deepseek.com'), (['compatible'], 'https://example.com')]:
    try:
        build_ai_client(SimpleNamespace(ai=SimpleNamespace(provider_priority=providers, base_url=url)))
    except ConfigError:
        pass
    else:
        raise AssertionError('public restriction disabled')
assert 'qq_digest.ai.bridge' not in sys.modules
"""
    subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path / 'missing.json')], check=True)
