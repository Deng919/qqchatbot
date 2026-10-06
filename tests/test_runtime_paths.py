from pathlib import Path

from qq_digest import runtime_paths


def test_public_runtime_paths_are_per_user_and_separate(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_paths, 'is_public_distribution', lambda: True)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'user'))
    monkeypatch.setattr(runtime_paths.Path, 'home', lambda: tmp_path / 'home')
    monkeypatch.setattr(runtime_paths.sys, 'executable', str(tmp_path / 'programs' / 'beta' / 'QQDigestDesktop.exe'))
    assert runtime_paths.cache_root() == tmp_path / 'user' / 'QQDigest' / 'Cache'
    assert runtime_paths.download_root() == tmp_path / 'home' / 'Downloads'
    assert runtime_paths.release_root() == tmp_path / 'programs'


def test_development_locations_stay_compatible(monkeypatch):
    monkeypatch.setattr(runtime_paths, 'is_public_distribution', lambda: False)
    assert runtime_paths.cache_root() == Path(r'D:\Cache\QQDigestDesktop')
    assert runtime_paths.download_root() == Path(r'D:\Downloads')
    assert runtime_paths.release_root() == Path(r'D:\Apps')
