import shutil
import subprocess
from pathlib import Path

import pytest


def test_generation_preview_and_busy_flow():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for generation interaction regression')
    result=subprocess.run([node,'tests/summary_generation.cjs'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
