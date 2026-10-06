import json

from scripts import build_desktop


def test_public_command_excludes_bridge_and_bundles_profile(tmp_path):
    command = build_desktop.build_command(tmp_path, public=True, version='0.2.0-beta.1')
    assert '--exclude-module' in command
    assert 'qq_digest.ai.bridge' in command
    assert '--runtime-hook' in command
    assert 'sys._qq_digest_public_build = True' in (tmp_path / 'public_runtime_hook.py').read_text()
    profile = json.loads((tmp_path / 'distribution.json').read_text(encoding='utf-8'))
    assert profile == {'edition': 'deepseek-only', 'version': '0.2.0-beta.1'}
    assert any('distribution.json;qq_digest' in value for value in command)


def test_public_finalize_does_not_copy_launcher_or_register_owner(tmp_path, monkeypatch):
    release = tmp_path / 'payload'
    release.mkdir()
    (release / 'QQDigestDesktop.exe').write_bytes(b'synthetic program')
    from qq_digest.desktop_releases import ReleaseRegistry
    monkeypatch.setattr(ReleaseRegistry, 'register', lambda *args: (_ for _ in ()).throw(AssertionError('owner registry accessed')))
    build_desktop.finalize_release(release, tmp_path / 'private.yaml', public=True, version='0.2.0-beta.1')
    assert not (release / 'launcher.json').exists()
    manifest = json.loads((release / 'release.json').read_text(encoding='utf-8'))
    assert manifest['compatibility'] == 'archive-2026-10-06-deepseek-only'
    assert manifest['backend_id'].endswith('-deepseek-only')
    assert manifest['version'] == '0.2.0-beta.1'
