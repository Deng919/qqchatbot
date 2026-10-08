import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_storage_ui_async_policy_and_cleanup_safety():
    script = ROOT / 'qq_digest/web/templates/storage_management_script.html'
    assert script.is_file(), 'Storage management interaction script is not implemented'
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for storage interaction regression')
    result = subprocess.run([node, 'tests/storage_ui.cjs'], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_storage_card_integrates_with_existing_category_and_wraps_paths():
    card = ROOT / 'qq_digest/web/templates/storage_management.html'
    assert card.is_file(), 'Storage management card is not implemented'
    html = card.read_text(encoding='utf-8')
    settings = (ROOT / 'qq_digest/web/templates/settings.html').read_text(encoding='utf-8')
    assert 'data-settings-panel="storage"' in html
    assert '{% include "storage_management.html" %}' in settings
    assert settings.index('include "settings_categories_script.html"') < settings.index('include "storage_management_script.html"')
    assert '旧备份保留。' not in settings
    assert 'href="/settings?section=storage"' in settings
    assert 'overflow-wrap: anywhere' in html
    assert 'flex-wrap: wrap' in html
    assert '@media (max-width: 700px)' in html
    assert 'max-height:' in html and 'overflow-y: auto' in html
    for field in ['backup-days', 'backup-keep', 'cache-days']:
        assert f'for="storage-{field}"' in html
