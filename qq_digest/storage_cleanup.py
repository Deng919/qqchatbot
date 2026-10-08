"""Fingerprints and recoverable isolation for bounded storage cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

from .storage_files import safe_path


class CleanupError(OSError):
    def __init__(self, message, removed_bytes=None):
        super().__init__(message)
        self.removed_bytes = removed_bytes


def fingerprint(path, root):
    path = safe_path(path, root)
    digest = hashlib.sha256()
    count = size = 0
    top = path.lstat()
    root_stat = safe_path(root).lstat()

    def visit(item):
        nonlocal count, size
        safe_path(item, root)
        before = item.lstat()
        relative = str(item.relative_to(path)) if item != path else '.'
        if stat.S_ISDIR(before.st_mode):
            digest.update(json.dumps([relative, before.st_ino, before.st_mtime_ns, 'dir']).encode())
            for child in sorted(item.iterdir()): visit(child)
        elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
            count += 1
            if count > 100000: raise ValueError('候选目录文件过多，已保留')
            size += before.st_size
            digest.update(json.dumps([relative, before.st_ino, before.st_size, before.st_mtime_ns]).encode())
            with item.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
        else:
            raise ValueError('含链接或特殊文件，已保留')
        after = item.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('扫描期间文件变化，请重新预览')

    visit(path)
    return {'digest': digest.hexdigest(), 'size_bytes': size, 'file_count': count,
            'device': top.st_dev, 'inode': top.st_ino, 'mtime_ns': top.st_mtime_ns,
            'root_device': root_stat.st_dev, 'root_inode': root_stat.st_ino}


def _write(path, data):
    temporary = path.with_suffix('.new')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def isolate_delete(candidate, owner):
    """Caller holds root/lease locks. Persist recovery intent before renaming."""
    root = safe_path(candidate['root'])
    original = safe_path(candidate['path'], root)
    if original.parent != root: raise ValueError('候选必须位于允许目录的直接子级')
    if fingerprint(original, root) != candidate['fingerprint']:
        raise ValueError('文件已变化，已跳过')
    identity = uuid.uuid4().hex
    quarantine = root / ('.qqdigest-trash-' + identity)
    journal = root / ('.qqdigest-cleanup-' + identity + '.json')
    _write(journal, {'version': 1, 'owner': str(owner), 'original': original.name,
                     'quarantine': quarantine.name, 'kind': candidate['kind'],
                     'fingerprint': candidate['fingerprint']})
    try:
        safe_path(original, root)
        original.rename(quarantine)
        if fingerprint(quarantine, root) != candidate['fingerprint']:
            raise ValueError('隔离后文件变化，已保留')
        safe_path(quarantine, root)
        if quarantine.is_dir(): shutil.rmtree(quarantine)
        else: quarantine.unlink()
        journal.unlink()
    except Exception as exc:
        removed = None
        try:
            if quarantine.exists() and not original.exists():
                safe_path(quarantine, root)
                quarantine.rename(original)
            if original.exists() and not quarantine.exists():
                remaining = fingerprint(original, root)['size_bytes']
                removed = max(0, candidate['size_bytes'] - remaining)
                journal.unlink(missing_ok=True)
            elif not quarantine.exists() and not original.exists():
                removed = candidate['size_bytes']
        except (OSError, ValueError):
            pass  # The journal and quarantine retain the recovery location.
        raise CleanupError(f'{exc}；清理位置：{original}；恢复记录：{journal}', removed) from exc


def recover_isolation(root, owner):
    """Only restore recognized interrupted candidates. Never delete retained content."""
    root = safe_path(root)
    warnings = []
    for journal in root.glob('.qqdigest-cleanup-*.json'):
        try:
            safe_path(journal, root)
            if journal.stat().st_size > 8192: raise ValueError('清理记录过大')
            data = json.loads(journal.read_text(encoding='utf-8'))
            if not isinstance(data, dict): raise ValueError('清理记录格式无效')
            identity = journal.name.removeprefix('.qqdigest-cleanup-').removesuffix('.json')
            if (len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity)
                    or data.get('version') != 1 or os.path.normcase(data.get('owner', '')) != os.path.normcase(str(owner))
                    or data.get('quarantine') != '.qqdigest-trash-' + identity
                    or data.get('kind') not in ('backup', 'cache')):
                raise ValueError('无法确认清理记录归属')
            name = data['original']
            if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
                raise ValueError('清理记录路径无效')
            if data['kind'] == 'backup':
                if not name.startswith('QQDigest-automatic-') or not name.endswith('.zip'):
                    raise ValueError('清理记录类型无效')
            elif not name.startswith(('qqdigest-backup-', 'qqdigest-check-', 'refresh-')):
                raise ValueError('清理记录类型无效')
            original = root / name
            quarantine = root / data['quarantine']
            if quarantine.exists():
                if fingerprint(quarantine, root) != data['fingerprint']:
                    raise ValueError('隔离内容已变化，请检查保留位置')
                if original.exists(): raise ValueError('原位置已有文件，隔离内容已保留')
                safe_path(original, root)
                quarantine.rename(original)
                warnings.append(f'已恢复中断清理：{original}')
            journal.unlink()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append(f'{journal}: {exc}')
    return warnings
