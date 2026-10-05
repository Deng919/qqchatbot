import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_contains_renderable_login_template(tmp_path):
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / 'wheel-source'
    source.mkdir()
    shutil.copy2(project / 'pyproject.toml', source / 'pyproject.toml')
    shutil.copytree(project / 'qq_digest', source / 'qq_digest',
                    ignore=shutil.ignore_patterns('__pycache__'))
    result = subprocess.run([sys.executable, '-c',
        "from setuptools.build_meta import build_wheel; build_wheel('dist')"],
        cwd=source, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next((source / 'dist').glob('*.whl'))
    with zipfile.ZipFile(wheel) as packaged:
        names = packaged.namelist()
        assert 'qq_digest/web/templates/login.html' in names
        assert 'qq_digest/web/templates/base.html' in names
        assert not any('reports (1)' in name for name in names)
        packaged.extractall(tmp_path / 'installed')
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(tmp_path / 'installed/qq_digest/web/templates'))
    assert 'QQ Digest' in env.get_template('login.html').render()
