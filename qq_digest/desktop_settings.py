"""Local desktop settings and safe data transfer helpers."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import winreg
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import yaml

from .config import load_config
from .backup_restore import (BACKUP_ROOT, CONFIG_PATH_FIELDS, DATA_FOLDERS,
                             TEMP_ROOT, backup_data, inspect_backup, restore_backup)
from .operations import OperationBusy


from .runtime_paths import download_root, release_root

EXPORT_ROOT = download_root() / 'QQDigestReports'
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "QQDigestDesktop"


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def migrate_storage(config_path: Path, install_dir: Path, destination: Path) -> dict:
    """Copy all managed data, rebase database paths, then atomically switch the launcher."""
    config_path = Path(config_path).resolve()
    install_dir = Path(install_dir).resolve()
    launcher = install_dir / "launcher.json"
    if not launcher.is_file():
        raise ValueError("只支持已打包的桌面程序更改存储位置")
    source = load_config(config_path).data_dir.resolve()
    destination = Path(destination).expanduser().resolve()
    if _inside(destination, source) or _inside(source, destination):
        raise ValueError("新位置不能位于现有数据目录内，也不能包含现有数据目录")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("请选择一个空文件夹或尚不存在的文件夹")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        (destination / "config").mkdir()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        for section, key in CONFIG_PATH_FIELDS:
            value = (raw.get(section) or {}).get(key)
            if not value:
                continue
            old_path = Path(value).expanduser()
            if not old_path.is_absolute():
                continue
            old_path = old_path.resolve()
            if _inside(old_path, source):
                relative = old_path.relative_to(source)
                new_path = destination / relative
                if old_path.is_file():
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old_path, new_path)
                raw[section][key] = str(new_path)
        new_config = destination / "config" / "config.yaml"
        new_config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for folder in DATA_FOLDERS:
            old = source / folder
            if old.is_dir():
                shutil.copytree(old, destination / folder, dirs_exist_ok=True)
        old_db = source / "archive" / "archive.sqlite"
        if old_db.is_file():
            new_db = destination / "archive" / "archive.sqlite"
            new_db.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(f"file:{old_db.as_posix()}?mode=ro", uri=True)) as original:
                with closing(sqlite3.connect(new_db)) as copied:
                    original.backup(copied)
                    for table in ("reports", "manual_reports", "knowledge_items"):
                        columns = ("markdown_path", "json_path") if table != "knowledge_items" else ("markdown_path",)
                        for column in columns:
                            rows = copied.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
                            for rowid, value in rows:
                                if not value:
                                    continue
                                path = Path(value)
                                if path.is_absolute() and _inside(path, source):
                                    copied.execute(
                                        f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                        (str(destination / path.relative_to(source)), rowid),
                                    )
                    copied.commit()
        load_config(new_config)
        _atomic_json(launcher, {"config_path": str(new_config)})
    except Exception:
        # Leave the original data and launcher untouched. A partial target can be inspected.
        raise
    return {"path": str(destination), "restart_required": True}


def export_reports(archive_path: Path, destination: Path, format: str = "both") -> dict:
    if format not in ("markdown", "json", "both"):
        raise ValueError("不支持的导出格式")
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError("尚无消息摘要数据库")
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    folder = destination / ("QQDigest-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    folder.mkdir()
    exported = missing = reports = 0
    try:
        with closing(sqlite3.connect(f"file:{archive_path.as_posix()}?mode=ro", uri=True)) as db:
            for table, id_column, label in (("reports", "report_id", "daily"),
                                            ("manual_reports", "manual_report_id", "range")):
                for report_id, markdown, report_json in db.execute(
                    f"SELECT {id_column}, markdown_path, json_path FROM {table} ORDER BY {id_column}"
                ):
                    reports += 1
                    for kind, path_value, extension in (("markdown", markdown, ".md"),
                                                        ("json", report_json, ".json")):
                        if format not in (kind, "both"):
                            continue
                        source = Path(path_value)
                        if source.is_file():
                            shutil.copy2(source, folder / f"{label}-{report_id}{extension}")
                            exported += 1
                        else:
                            missing += 1
    except Exception:
        # Preserve partial results for troubleshooting, without touching source reports.
        raise
    return {"path": str(folder), "reports": reports, "files": exported, "missing": missing}


def auto_start_enabled(executable: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
        return value == f'"{Path(executable).resolve()}"'
    except FileNotFoundError:
        return False


def set_auto_start(executable: Path, enabled: bool) -> bool:
    if sys.platform != "win32" or not Path(executable).is_file():
        raise ValueError("开机自启只支持已打包的 Windows 桌面程序")
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, f'"{Path(executable).resolve()}"')
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass
    return auto_start_enabled(executable)


class DesktopBridge:
    def __init__(self, install_dir: Path, config_path: Path, executable: Path, *, gui,
                 backup_root: Path = BACKUP_ROOT, temp_root: Path = TEMP_ROOT):
        self._install_dir = Path(install_dir)
        self._config_path = Path(config_path)
        self._executable = Path(executable)
        self._gui = gui
        self._window = None
        self._backup_root = Path(backup_root)
        self._temp_root = Path(temp_root)
        self._backup_lock = Lock()
        self._last_backup_error = ""
        self._operations = None
        self._update_claim = None
        self._update_guard = Lock()
        self._owns_service = False
        self._runtime_url = ''

    def _updates(self):
        from .desktop_releases import ReleaseRegistry
        from .desktop_updates import STATE_DIR, UpdateService
        launcher = json.loads((self._install_dir / 'launcher.json').read_text(encoding='utf-8'))
        launched_config = Path(launcher['config_path'])
        if not launched_config.is_absolute():
            launched_config = self._install_dir / launched_config
        if launched_config.resolve() != self._config_path.resolve():
            raise ValueError('存储位置已切换，请先重启程序')
        registry = ReleaseRegistry(release_root(), STATE_DIR)
        registry.register(self._install_dir)
        config = load_config(self._config_path)
        data_paths = [config.data_dir, config.archive_path, config.ntqq.db_dir,
                      *config.resolve_knowledge_paths().values()]
        for section, field in CONFIG_PATH_FIELDS:
            value = getattr(getattr(config, section), field, '')
            if value:
                path = Path(value).expanduser()
                data_paths.append(path if path.is_absolute() else config.data_dir / path)
        def verified_backup():
            result = backup_data(self._config_path, self._backup_root,
                                 temp_root=self._temp_root, kind='before-update')
            inspect_backup(Path(result['path']), temp_root=self._temp_root)
            return result
        return UpdateService(registry, self._install_dir, self._config_path,
                             verified_backup, data_paths=data_paths)

    def get_updates(self):
        from .desktop_updates import ACTIVE, read_transaction
        from .desktop_update_worker import process_alive
        service = self._updates()
        result = service.snapshot()
        transaction = read_transaction(service.registry)
        worker_pid = transaction.get('worker_pid')
        recoverable = transaction.get('status') in ACTIVE | {'recovery_failed'}
        if worker_pid and recoverable:
            recoverable = not process_alive(worker_pid)
        result['transaction']['recoverable'] = bool(recoverable)
        return result

    def clean_version(self, version, digest):
        with self._claim_operation('version_mutation'):
            service = self._updates()
            return service.registry.clean(version, digest,
                protected=[service.current()['version']], data_paths=service.data_paths)

    def release_update_claim(self, expected=None):
        with self._update_guard:
            if expected is not None and self._update_claim is not expected:
                return
            claim, self._update_claim = self._update_claim, None
        if claim is not None:
            claim.__exit__(None, None, None)

    def _start_update_worker(self, service):
        import subprocess
        from threading import Timer, Thread
        with self._update_guard:
            launched_claim = self._update_claim
        child = subprocess.Popen([str(self._executable), '--update-worker', str(service.registry.state_dir)],
            cwd=self._install_dir,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        # Return the API response before closing the HTTP server and its window.
        timer = Timer(1, self._window.destroy)
        timer.daemon = True
        timer.start()
        def release_if_canceled():
            child.wait()
            self.release_update_claim(expected=launched_claim)
        Thread(target=release_if_canceled, name='qq-digest-update-watch', daemon=True).start()

    def switch_version(self, version, digest):
        if not self._owns_service or self._window is None:
            raise ValueError('请在拥有本地服务的桌面窗口中切换版本')
        claim = self._claim_operation('version_mutation')
        claim.__enter__()
        with self._update_guard:
            self._update_claim = claim
        service = None
        prepared = False
        try:
            with self._backup_lock:
                service = self._updates()
                result = service.prepare(version, digest, parent_pid=os.getpid(), url=self._runtime_url)
                prepared = True
                self._start_update_worker(service)
                return result
        except Exception:
            if prepared and service is not None:
                from .desktop_updates import atomic_json, read_transaction
                transaction = read_transaction(service.registry)
                if transaction.get('status') == 'prepared':
                    transaction.update(status='canceled', error='工作进程未能启动，当前版本未改变')
                    atomic_json(service.registry.state_dir / 'transaction.json', transaction)
            self.release_update_claim()
            raise

    def recover_version(self):
        from .desktop_update_worker import process_alive
        from .desktop_updates import ACTIVE, atomic_json, read_transaction
        if not self._owns_service or self._window is None:
            raise ValueError('请先打开原版本桌面窗口')
        claim = self._claim_operation('version_mutation')
        claim.__enter__()
        with self._update_guard:
            self._update_claim = claim
        try:
            with self._backup_lock:
                service = self._updates()
                transaction = read_transaction(service.registry)
                if transaction.get('status') not in ACTIVE | {'recovery_failed'}:
                    raise ValueError('没有需要恢复的切换')
                if transaction.get('worker_pid') and process_alive(transaction['worker_pid']):
                    raise ValueError('切换仍在运行，请稍后重试')
                service.backup()
                transaction.update(status='recovering', parent_pid=os.getpid())
                atomic_json(service.registry.state_dir / 'transaction.json', transaction)
            self._start_update_worker(service)
        except Exception:
            self.release_update_claim()
            raise
        return {'status': 'recovering'}

    def _claim_operation(self, kind: str):
        return self._operations.claim(kind) if self._operations is not None else nullcontext()

    def _preferences(self) -> dict:
        path = self._install_dir / "desktop-preferences.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_preferences(self, values: dict) -> None:
        saved = self._preferences()
        saved.update(values)
        _atomic_json(self._install_dir / "desktop-preferences.json", saved)

    def list_backups(self) -> list[dict]:
        if not self._backup_root.is_dir():
            return []
        paths = sorted(self._backup_root.glob("QQDigest-*.zip"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        return [{"path": str(path), "name": path.name,
                 "size_bytes": path.stat().st_size,
                 "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()}
                for path in paths[:10] if path.is_file()]

    def get_settings(self) -> dict:
        config = load_config(self._config_path)
        return {
            "storage_path": str(config.data_dir),
            "export_path": self._preferences().get("export_path", str(EXPORT_ROOT)),
            "backup_path": str(self._backup_root),
            "backup_schedule": self._preferences().get("backup_schedule", "weekly"),
            "recent_backups": self.list_backups(),
            "backup_error": self._last_backup_error,
            "auto_start": auto_start_enabled(self._executable),
            "packaged": (self._install_dir / "launcher.json").is_file(),
        }

    def choose_folder(self, kind: str = "storage") -> str:
        if self._window is None:
            raise RuntimeError("桌面窗口尚未就绪")
        from System import Action
        from System.Windows.Forms import DialogResult, FolderBrowserDialog

        initial = self._preferences().get("export_path", str(EXPORT_ROOT)) if kind == "export" else str(load_config(self._config_path).data_dir)
        if not Path(initial).is_dir():
            initial = str(Path(initial).parent)
        chosen = [""]
        def show_dialog():
            self._window.native.Activate()
            dialog = FolderBrowserDialog()
            dialog.SelectedPath = initial
            dialog.ShowNewFolderButton = True
            try:
                if dialog.ShowDialog(self._window.native) == DialogResult.OK:
                    chosen[0] = str(dialog.SelectedPath)
            finally:
                dialog.Dispose()
        self._window.native.Invoke(Action(show_dialog))
        return chosen[0]

    def migrate_storage(self, destination: str) -> dict:
        if not destination:
            raise ValueError("请先选择新的存储位置")
        with self._claim_operation("storage_mutation"), self._backup_lock:
            return migrate_storage(self._config_path, self._install_dir, Path(destination))

    def export_reports(self, destination: str, format: str) -> dict:
        config = load_config(self._config_path)
        selected = Path(destination or EXPORT_ROOT).expanduser().resolve()
        result = export_reports(config.archive_path, selected, format)
        self._save_preferences({"export_path": str(selected)})
        return result

    def backup_data(self) -> dict:
        with self._claim_operation("backup"), self._backup_lock:
            return backup_data(self._config_path, self._backup_root, temp_root=self._temp_root)

    def set_backup_schedule(self, schedule: str) -> dict:
        if schedule not in {"off", "daily", "weekly"}:
            raise ValueError("备份频率只支持关闭、每天或每周")
        self._save_preferences({"backup_schedule": schedule})
        return {"backup_schedule": schedule}

    def maybe_scheduled_backup(self, now: datetime | None = None) -> dict | None:
        schedule = self._preferences().get("backup_schedule", "weekly")
        if schedule == "off":
            return None
        if schedule not in {"daily", "weekly"}:
            raise ValueError("备份频率设置无效")
        now = now or datetime.now(timezone.utc)
        interval = timedelta(days=1 if schedule == "daily" else 7)
        try:
            with self._claim_operation("backup"), self._backup_lock:
                backups = self.list_backups()
                if backups:
                    latest = datetime.fromisoformat(backups[0]["modified_at"]).astimezone(timezone.utc)
                    if now - latest < interval:
                        return None
                result = backup_data(self._config_path, self._backup_root,
                                     temp_root=self._temp_root, kind="automatic")
                self._last_backup_error = ""
                return result
        except OperationBusy:
            return None

    def run_backup_schedule(self, stop_event) -> None:
        import logging
        while not stop_event.is_set():
            try:
                self.maybe_scheduled_backup()
            except Exception as exc:
                self._last_backup_error = str(exc)
                logging.getLogger("qq_digest.backup").exception("自动备份失败")
            stop_event.wait(3600)

    def choose_backup_file(self) -> str:
        if self._window is None:
            raise RuntimeError("桌面窗口尚未就绪")
        from System import Action
        from System.Windows.Forms import DialogResult, OpenFileDialog

        chosen = [""]
        def show_dialog():
            self._window.native.Activate()
            dialog = OpenFileDialog()
            dialog.Filter = "ZIP 备份 (*.zip)|*.zip"
            dialog.InitialDirectory = str(self._backup_root)
            try:
                if dialog.ShowDialog(self._window.native) == DialogResult.OK:
                    chosen[0] = str(dialog.FileName)
            finally:
                dialog.Dispose()
        self._window.native.Invoke(Action(show_dialog))
        return chosen[0]

    def preview_restore(self, path: str) -> dict:
        return inspect_backup(Path(path), temp_root=self._temp_root)

    def restore_backup(self, path: str, destination: str, expected_sha256: str) -> dict:
        with self._claim_operation("storage_mutation"), self._backup_lock:
            return restore_backup(Path(path), Path(destination), self._config_path,
                                  self._install_dir, backup_root=self._backup_root,
                                  temp_root=self._temp_root, expected_sha256=expected_sha256)

    def open_folder(self, kind: str) -> dict:
        if kind == "storage":
            path = load_config(self._config_path).data_dir
        elif kind == "export":
            path = Path(self._preferences().get("export_path", str(EXPORT_ROOT)))
        elif kind == "backup":
            path = self._backup_root
        else:
            raise ValueError("未知目录")
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)
        return {"path": str(path)}

    def set_auto_start(self, enabled: bool) -> dict:
        return {"auto_start": set_auto_start(self._executable, enabled)}
