from types import SimpleNamespace
from qq_digest.web.app import templates


def test_ai_settings_contains_multi_connection_controls():
    html = templates.env.get_template('ai_connection_settings.html').render(
        features={}, request=SimpleNamespace(url=SimpleNamespace(path='/settings')))
    assert 'multi-ai-form' in html
    assert '/api/ai-settings/connections' in html
    assert 'ai-purpose-summary' in html
    assert 'API Key（留空保留原值）' in html


def test_multi_ai_script_preserves_drafts_and_versions():
    import subprocess
    subprocess.run(['node','tests/multi_ai_ui.cjs'],check=True)
