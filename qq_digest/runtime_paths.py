"""Runtime locations for local and public desktop editions."""
from pathlib import Path
import os
import sys


def is_public_distribution():
    from .distribution import is_public_distribution as public
    return public()


def cache_root():
    if is_public_distribution():
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'QQDigest' / 'Cache'
    return Path(r'D:\Cache\QQDigestDesktop')


def download_root():
    if is_public_distribution():
        return Path.home() / 'Downloads'
    return Path(r'D:\Downloads')


def release_root():
    if is_public_distribution():
        return Path(sys.executable).resolve().parent.parent
    return Path(r'D:\Apps')
