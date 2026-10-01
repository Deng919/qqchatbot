import shutil
import subprocess
from pathlib import Path

import pytest


def test_report_version_ui_async_races():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for template JavaScript regression checks')
    result = subprocess.run([node,'tests/report_revisions_ui.cjs'],
                            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
