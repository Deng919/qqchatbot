"""Verified local archives and non-destructive desktop data restore."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

from .config import ConfigError, load_config


BACKUP_ROOT = Path(r"D:\Downloads\QQDigestBackups")
TEMP_ROOT = Path(r"D:\Cache\QQDigestDesktop")
DATA_FOLDERS = ("reports", "knowledge", "work", "logs")
CONFIG_PATH_FIELDS = (("knowledge", "resource_path"), ("knowledge", "experience_path"),
                      ("ai", "api_key_file"), ("ai", "ui_api_key_file"),
                      ("security", "session_secret_file"))
MAX_FILES = 100_000
MAX_UNCOMPRESSED_BYTES = 50 * 1024**3
MAX_MANIFEST_BYTES = 16 * 1024**2


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_names(archive: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    total = 0
    for info in archive.infolist():
        name = info.filename
        normalized = name[:-1] if info.is_dir() and name.endswith("/") else name
        parts = normalized.split("/")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
        if (name.startswith("/") or "\\" in name or not parts or
                any(part in (".", "..", "") or part.endswith((".", " ")) or
                    any(char in '<>:"|?*' for char in part) or
                    part.split(".")[0].upper() in reserved for part in parts) or
                PurePosixPath(normalized).as_posix() != normalized or
                parts[0] not in {"config", "archive", *DATA_FOLDERS, "manifest.json"} or
                (parts[0] == "manifest.json" and name != "manifest.json")):
            raise ValueError(f"备份包含不安全的文件路径：{name}")
        if name.casefold() in seen:
            raise ValueError(f"备份包含重复路径：{name}")
        seen.add(name.casefold())
        if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
            raise ValueError(f"备份包含不安全的文件路径：{name}")
        if name == "manifest.json" and info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("备份校验清单过大")
        if info.is_dir():
            continue
        total += info.file_size
        names.append(name)
        if len(names) > MAX_FILES or total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("备份文件数量或大小超出安全限制")
    if "config/config.yaml" not in names or "archive/archive.sqlite" not in names:
        raise ValueError("备份缺少配置或消息数据库")
    return names


def _count(db: sqlite3.Connection, table: str) -> int:
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return 0


def _parse_config(content: bytes) -> dict:
    try:
        raw = yaml.safe_load(content.decode("utf-8-sig"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("备份配置文件无效") from exc
    if (not isinstance(raw, dict) or
            not isinstance(raw.get("security"), dict) or
            not raw["security"].get("web_password_hash") or
            not isinstance(raw.get("ai"), dict) or
            not raw["ai"].get("model")):
        raise ValueError("备份配置文件缺少必要设置")
    return raw


def _validate_core_schema(db: sqlite3.Connection) -> None:
    required = {
        "messages": {"msg_id", "group_id", "sender_qq", "timestamp",
                     "message_type", "text", "content_json", "raw_digest",
                     "source_id", "device_id", "collected_at"},
        "groups": {"group_id", "name"},
        "reports": {"report_id", "group_id", "markdown_path", "json_path"},
    }
    for table, columns in required.items():
        table_info = db.execute(f"PRAGMA table_info({table})").fetchall()
        actual = {row[1] for row in table_info}
        if not columns <= actual:
            raise ValueError(f"消息数据库结构不兼容：{table}")
        if table == "messages":
            primary_key = [row[1] for row in sorted(
                (row for row in table_info if row[5] > 0), key=lambda row: row[5]
            )]
            if primary_key != ["group_id", "msg_id"]:
                raise ValueError("消息数据库结构不兼容：messages 缺少群消息联合主键")


def backup_data(config_path: Path, destination: Path = BACKUP_ROOT,
                *, temp_root: Path = TEMP_ROOT, kind: str = "manual") -> dict:
    """Write an SQLite snapshot and SHA-256 manifest into one ZIP."""
    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    if not config.archive_path.is_file():
        raise ValueError("尚无消息数据库，无法生成可恢复的备份")
    destination = Path(destination).expanduser().resolve()
    temp_root = Path(temp_root).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    backup = destination / ("QQDigest-" + kind + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".zip")
    partial = backup.with_suffix(".partial")
    files: dict[str, Path] = {"config/config.yaml": config_path}
    try:
        with tempfile.TemporaryDirectory(prefix="qqdigest-backup-", dir=temp_root) as temporary:
            snapshot = Path(temporary) / "archive.sqlite"
            with closing(sqlite3.connect(f"file:{config.archive_path.as_posix()}?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as copied:
                    source.backup(copied)
            files["archive/archive.sqlite"] = snapshot
            for folder in DATA_FOLDERS:
                root = config.data_dir / folder
                if root.is_dir():
                    for path in root.rglob("*"):
                        if path.is_file() and not path.is_symlink():
                            relative = path.relative_to(config.data_dir)
                            if relative.parts[:2] == ("work", "snapshots"):
                                continue
                            files[relative.as_posix()] = path
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            for section, key in CONFIG_PATH_FIELDS:
                value = (raw.get(section) or {}).get(key)
                if not value:
                    continue
                path = Path(value).expanduser()
                path = (path if path.is_absolute() else config.data_dir / path).resolve()
                if path.is_file() and not path.is_symlink() and _inside(path, config.data_dir):
                    files[path.relative_to(config.data_dir).as_posix()] = path
            stable_files: dict[str, Path] = {}
            for name, path in files.items():
                if name == "archive/archive.sqlite":
                    stable_files[name] = snapshot
                    continue
                stable = Path(temporary) / "files" / name
                stable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, stable)
                stable_files[name] = stable
            files = stable_files
            manifest = {
                "format_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_data_dir": str(config.data_dir),
                "kind": kind,
                "files": {name: {"sha256": _sha256(path), "size": path.stat().st_size}
                          for name, path in sorted(files.items())},
            }
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, path in files.items():
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(partial) as completed:
                _archive_names(completed)
                if completed.testzip() is not None:
                    raise ValueError("新备份 ZIP 校验失败")
            os.replace(partial, backup)
        return {"path": str(backup), "size_bytes": backup.stat().st_size}
    except Exception:
        partial.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        raise


def inspect_backup(path: Path, *, temp_root: Path = TEMP_ROOT) -> dict:
    """Verify ZIP paths, CRC, file hashes and SQLite integrity before preview."""
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("请选择存在的 ZIP 备份文件")
    temp_root = Path(temp_root).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as archive:
            names = _archive_names(archive)
            if archive.testzip() is not None:
                raise ValueError("备份 ZIP 校验失败")
            manifest = None
            if "manifest.json" in names:
                try:
                    manifest = json.loads(archive.read("manifest.json"))
                    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
                        raise ValueError("备份校验清单无效")
                    recorded = manifest["files"]
                    if manifest["format_version"] != 1 or set(recorded) != set(names) - {"manifest.json"}:
                        raise ValueError("备份校验清单与文件不一致")
                    for name, expected in recorded.items():
                        digest = hashlib.sha256()
                        size = 0
                        with archive.open(name) as member:
                            for chunk in iter(lambda: member.read(1024 * 1024), b""):
                                size += len(chunk)
                                digest.update(chunk)
                        if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                            raise ValueError(f"备份文件校验失败：{name}")
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("备份校验清单无效") from exc
            _parse_config(archive.read("config/config.yaml"))
            with tempfile.TemporaryDirectory(prefix="qqdigest-check-", dir=temp_root) as temporary:
                database = Path(temporary) / "archive.sqlite"
                with archive.open("archive/archive.sqlite") as source, database.open("wb") as target:
                    shutil.copyfileobj(source, target)
                with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("消息数据库完整性校验失败")
                    _validate_core_schema(db)
                    counts = {"messages": _count(db, "messages"),
                              "reports": _count(db, "reports") + _count(db, "manual_reports"),
                              "knowledge_items": _count(db, "knowledge_items")}
            return {"valid": True, "verified": manifest is not None,
                    "legacy": manifest is None, "path": str(path), "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size, "file_count": len(names) - (manifest is not None),
                    "created_at": manifest.get("created_at", "") if manifest else "",
                    "source_data_dir": manifest.get("source_data_dir", "") if manifest else "",
                    **counts}
    except (zipfile.BadZipFile, sqlite3.DatabaseError) as exc:
        raise ValueError(f"备份完整性校验失败：{exc}") from exc


def _legacy_root(database: Path, config_path: Path) -> Path | None:
    with closing(sqlite3.connect(database)) as db:
        for table, column in (("reports", "markdown_path"), ("manual_reports", "markdown_path"),
                              ("knowledge_items", "markdown_path")):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                continue
            for (value,) in db.execute(f"SELECT {column} FROM {table} WHERE {column} != '' LIMIT 10"):
                path = Path(value)
                for parent in path.parents:
                    if parent.name in DATA_FOLDERS:
                        return parent.parent
    raw = _parse_config(config_path.read_bytes())
    for section, key in (("security", "session_secret_file"),
                         ("knowledge", "resource_path"),
                         ("knowledge", "experience_path")):
        value = (raw.get(section) or {}).get(key)
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_absolute():
            for parent in path.parents:
                if parent.name in {"config", "knowledge"}:
                    return parent.parent
    return None


def restore_backup(path: Path, destination: Path, current_config_path: Path, install_dir: Path,
                   *, backup_root: Path = BACKUP_ROOT, temp_root: Path = TEMP_ROOT,
                   expected_sha256: str) -> dict:
    """Restore to empty storage; save current state and switch only after validation."""
    preview = inspect_backup(path, temp_root=temp_root)
    if preview["sha256"] != expected_sha256:
        raise ValueError("备份自预览后已变化，请重新预览")
    current_config_path = Path(current_config_path).resolve()
    current_root = load_config(current_config_path).data_dir.resolve()
    destination = Path(destination).expanduser().resolve()
    install_dir = Path(install_dir).resolve()
    launcher = install_dir / "launcher.json"
    if not launcher.is_file():
        raise ValueError("只支持已打包的桌面程序恢复数据")
    if _inside(destination, current_root) or _inside(current_root, destination):
        raise ValueError("恢复位置不能与当前数据目录重叠")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("请选择空文件夹或尚不存在的文件夹")
    destination.parent.mkdir(parents=True, exist_ok=True)
    safety = backup_data(current_config_path, backup_root, temp_root=temp_root, kind="before-restore")
    staging = Path(tempfile.mkdtemp(prefix="qqdigest-restore-", dir=destination.parent))
    moved = False
    try:
        with zipfile.ZipFile(path) as archive:
            for name in _archive_names(archive):
                if name == "manifest.json":
                    continue
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        old_root = (Path(preview["source_data_dir"]) if preview["source_data_dir"]
                    else _legacy_root(staging / "archive" / "archive.sqlite",
                                      staging / "config" / "config.yaml"))
        if old_root is not None:
            _rebase_paths_to(staging, old_root, destination)
        try:
            load_config(staging / "config" / "config.yaml", create_dirs=False)
        except ConfigError as exc:
            raise ValueError(f"备份配置无效：{exc}") from exc
        if _sha256(Path(path).expanduser().resolve()) != expected_sha256:
            raise ValueError("备份自预览后已变化，请重新预览")
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
        moved = True
        restored_config = load_config(destination / "config" / "config.yaml")
        secret_file = restored_config.security.session_secret_file
        if secret_file:
            secret_path = Path(secret_file).expanduser()
            secret_path = (secret_path if secret_path.is_absolute() else destination / secret_path).resolve()
            if not secret_path.is_file() and _inside(secret_path, destination):
                secret_path.parent.mkdir(parents=True, exist_ok=True)
                secret_path.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
            restored_config.resolve_session_secret()
        temporary = launcher.with_name(launcher.name + ".new")
        temporary.write_text(json.dumps({"config_path": str(destination / "config" / "config.yaml")}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, launcher)
        return {"path": str(destination), "safety_backup": safety["path"], "restart_required": True}
    finally:
        if not moved:
            shutil.rmtree(staging, ignore_errors=True)


def _rebase_paths_to(root: Path, old_root: Path, new_root: Path) -> None:
    config_path = root / "config" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for section, key in CONFIG_PATH_FIELDS:
        value = (raw.get(section) or {}).get(key)
        if value:
            path = Path(value).expanduser()
            if path.is_absolute() and _inside(path, old_root):
                raw[section][key] = str(new_root / path.relative_to(old_root))
    config_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    database = root / "archive" / "archive.sqlite"
    with closing(sqlite3.connect(database)) as db:
        for table, columns in (("reports", ("markdown_path", "json_path")),
                               ("manual_reports", ("markdown_path", "json_path")),
                               ("knowledge_items", ("markdown_path",))):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                continue
            for column in columns:
                for rowid, value in db.execute(f"SELECT rowid, {column} FROM {table}").fetchall():
                    if value:
                        path = Path(value)
                        if path.is_absolute() and _inside(path, old_root):
                            db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                       (str(new_root / path.relative_to(old_root)), rowid))
        db.commit()
