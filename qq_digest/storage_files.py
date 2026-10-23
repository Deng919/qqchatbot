"""Path checks and process leases shared by storage, backup, and refresh."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time


CACHE_MARKER = ".qqdigest-cache.json"
CACHE_PREFIXES = ("qqdigest-backup-", "qqdigest-check-", "refresh-")
_root_mutexes: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()
_held_roots = threading.local()


@dataclass
class _ActivityLease:
    owner: threading.Thread
    depth: int
    context: AbstractContextManager
    share_token: object | None = None


_activity_guard = threading.Lock()
_activities: dict[str, _ActivityLease] = {}


class StorageBusy(RuntimeError):
    """Another process or cache user holds the required storage lease."""


def safe_path(path: str | Path, root: str | Path | None = None) -> Path:
    """Return an absolute lexical path after rejecting links in its full chain.

    Do not resolve first: resolution would conceal a junction or symlink.
    Inspect the original chain too, so a link followed by ``..`` cannot hide.
    """
    expanded = os.path.expanduser(os.fspath(path))
    lexical = Path(expanded if os.path.isabs(expanded) else os.path.join(os.getcwd(), expanded))
    absolute = Path(os.path.abspath(lexical))
    for candidate in (*reversed(lexical.parents), lexical, *reversed(absolute.parents), absolute):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f"存储路径包含链接或重解析点：{candidate}")
    if root is not None:
        allowed = safe_path(root)
        if not absolute.is_relative_to(allowed):
            raise ValueError("存储路径超出允许目录")
    return absolute


def _normalized_owner(owner: str | Path) -> str:
    return os.path.normcase(str(safe_path(owner)))


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("缓存归属标记包含重复字段")
        result[key] = value
    return result


@contextmanager
def _file_lock(path: Path):
    path = safe_path(path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        opened = os.fstat(descriptor)
        current = safe_path(path).lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("存储锁文件身份无效")
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StorageBusy("存储目录正在被其他操作使用，请稍后重试") from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def root_lock(root: str | Path):
    """Serialize threads, reenter in the same thread, and fail fast across processes.

    The root must already exist. The persistent lock file must never be removed:
    replacing it could allow two processes to hold locks on different inodes.
    """
    root = safe_path(root)
    key = os.path.normcase(str(root))
    with _registry_lock:
        mutex = _root_mutexes.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_held_roots, "paths", None)
        if held is None:
            held = _held_roots.paths = set()
        if key in held:
            yield
            return
        with _file_lock(root / ".qqdigest-storage.lock"):
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)


@contextmanager
def activity_lock(root: str | Path, *, share_token=None):
    """Claim a storage operation immediately, allowing watcher-thread release.

    Same-thread nested claims share the outer OS lease. A coordinator may share
    its lease across threads after its own conflict check using an identity token.
    Unrelated coordinators and processes fail fast. The registry is guarded during entry and
    exit, so its mutex is never held while the operation runs or transferred
    between threads. Any final outstanding exit releases the original lease.
    """
    root = safe_path(root)
    key = os.path.normcase(str(root))
    # A transferred lease can outlive its owner, whose numeric ID may be reused.
    owner = threading.current_thread()
    with _activity_guard:
        lease = _activities.get(key)
        if lease is not None:
            if lease.owner is not owner and not (share_token is not None and lease.share_token is share_token):
                raise StorageBusy("存储目录正在被其他操作使用，请稍后重试")
            lease.depth += 1
        else:
            context = _file_lock(root / ".qqdigest-activity.lock")
            context.__enter__()
            lease = _ActivityLease(owner=owner, depth=1, context=context, share_token=share_token)
            _activities[key] = lease
    try:
        yield
    finally:
        with _activity_guard:
            lease.depth -= 1
            if lease.depth == 0:
                try:
                    lease.context.__exit__(None, None, None)
                finally:
                    del _activities[key]


@contextmanager
def cache_lock(path: str | Path):
    """Take an exclusive nonblocking lease without preventing directory rename.

    The stable sidecar is keyed by the original name and remains in the parent.
    Cleanup must hold this lease through fingerprinting, quarantine, and deletion.
    """
    path = safe_path(path)
    key = hashlib.sha256(os.path.normcase(path.name).encode("utf-8")).hexdigest()
    with _file_lock(path.parent / f".qqdigest-cache-{key}.lock"):
        yield


def cache_metadata(path: str | Path, owner: str | Path) -> dict:
    """Recognize only versioned cache markers belonging to this storage root."""
    try:
        path = safe_path(path)
        if not path.is_dir():
            raise ValueError("缓存目录不存在")
        marker = safe_path(path / CACHE_MARKER, path)
        info = marker.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
            raise ValueError("缓存归属标记无效")
        data = json.loads(marker.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
        if (not isinstance(data, dict) or
                set(data) != {"version", "owner", "created_at", "prefix"} or
                type(data["version"]) is not int or data["version"] != 1 or
                not isinstance(data["owner"], str) or not os.path.isabs(data["owner"]) or
                Path(data["owner"]) != safe_path(data["owner"]) or
                _normalized_owner(data["owner"]) != _normalized_owner(owner) or
                data["prefix"] not in CACHE_PREFIXES or
                not path.name.startswith(data["prefix"]) or path.name == data["prefix"] or
                not isinstance(data["created_at"], str)):
            raise ValueError("缓存归属标记无效")
        created = datetime.fromisoformat(data["created_at"])
        if created.tzinfo is None or created.utcoffset() != timezone.utc.utcoffset(created):
            raise ValueError("缓存创建时间必须使用 UTC")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError("缓存归属标记无效或不可读取") from exc


def _remove_temporary(path: Path, root: Path, identity: tuple[int, int]) -> None:
    path = safe_path(path, root)
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) != identity:
        raise ValueError("临时目录已被替换，保留供检查")
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in (*directories, *files):
            safe_path(Path(folder) / name, path)
    for attempt in range(4):
        try:
            shutil.rmtree(safe_path(path, root))
            return
        except PermissionError:
            if attempt == 3:
                raise
            # Windows security scanners and SQLite handles can hold a cache
            # directory briefly after the refresh has finished.
            time.sleep(0.1 * (attempt + 1))


@contextmanager
def managed_temporary(root: str | Path, prefix: str, owner: str | Path,
                      *, historical_owner: bool = False):
    """Create a recognized cache, lease its whole lifecycle, and clean on exit."""
    if prefix not in CACHE_PREFIXES:
        raise ValueError("不支持的缓存前缀")
    if historical_owner:
        # Archive provenance is only a lexical identity; never inspect its old
        # location. Actual cache paths and live owners retain link checks.
        if not Path(owner).is_absolute():
            raise ValueError("历史缓存归属必须使用绝对目录")
        owner_name = os.path.normcase(os.path.normpath(owner))
    else:
        owner_name = _normalized_owner(owner)
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = safe_path(tempfile.mkdtemp(prefix=prefix, dir=root), root)
    info = path.lstat()
    identity = (info.st_dev, info.st_ino)
    try:
        with cache_lock(path):
            try:
                marker = {"version": 1, "owner": owner_name, "prefix": prefix,
                          "created_at": datetime.now(timezone.utc).isoformat()}
                (path / CACHE_MARKER).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
                yield path
            finally:
                _remove_temporary(path, root, identity)
    except StorageBusy:
        # A guessed name must not cause us to remove a directory leased elsewhere.
        raise
