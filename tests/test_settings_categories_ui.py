import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('script',['settings_categories.cjs','group_details_async.cjs','workspace_status.cjs'])
def test_form_save_isolation_and_unsaved_drafts(script):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node required for template function regression')
    result=subprocess.run([node,str(Path('tests')/script)],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
