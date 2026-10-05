import shutil
import subprocess
from pathlib import Path

import pytest


def test_topic_navigation_corrections_and_delayed_responses():
    root = Path(__file__).resolve().parents[1]
    assert (root / 'qq_digest/web/templates/topics_script.html').is_file()
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for topic interaction regression')
    result = subprocess.run([node, 'tests/topic_tracking_ui.cjs'], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
