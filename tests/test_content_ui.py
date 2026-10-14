"""Execute the real template script behavioral regressions under Node."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_content_ui_behaviors():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for template script regressions')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests' / 'test_content_ui.cjs')],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
