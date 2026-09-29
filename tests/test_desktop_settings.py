import json
import sqlite3
from pathlib import Path

import pytest

from qq_digest.desktop_settings import DesktopBridge, backup_data, export_reports, migrate_storage


def _source(tmp_path):
    root = tmp_path / "source"
    (root / "config").mkdir(parents=True)
    config = root / "config" / "config.yaml"
    config.write_text(
        "security:\n  web_password_hash: test\nai:\n  model: test\n"
        "  base_url: https://example.com\n  api_key_env: TEST_KEY\n"
        "knowledge:\n  resource_path: knowledge/resources.md\n",
        encoding="utf-8",
    )
    (root / "archive").mkdir()
    db = sqlite3.connect(root / "archive" / "archive.sqlite")
    db.executescript("""
        CREATE TABLE reports (report_id INTEGER PRIMARY KEY, markdown_path TEXT, json_path TEXT);
        CREATE TABLE manual_reports (manual_report_id INTEGER PRIMARY KEY, markdown_path TEXT, json_path TEXT);
        CREATE TABLE knowledge_items (item_id TEXT PRIMARY KEY, markdown_path TEXT);
    """)
    (root / "reports").mkdir()
    markdown = root / "reports" / "daily.md"
    markdown.write_text("# 摘要", encoding="utf-8")
    report_json = root / "reports" / "daily.json"
    report_json.write_text("{}", encoding="utf-8")
    db.execute("INSERT INTO reports VALUES (1, ?, ?)", (str(markdown), str(report_json)))
    db.commit()
    db.close()
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    return root, config, install


def test_storage_migration_copies_database_and_rebases_paths(tmp_path):
    source, config, install = _source(tmp_path)
    destination = tmp_path / "new-data"
    result = migrate_storage(config, install, destination)
    assert result["restart_required"] is True
    assert (source / "reports" / "daily.md").is_file()
    assert (destination / "reports" / "daily.md").read_text(encoding="utf-8") == "# 摘要"
    assert json.loads((install / "launcher.json").read_text(encoding="utf-8"))["config_path"] == str(destination / "config" / "config.yaml")
    with sqlite3.connect(destination / "archive" / "archive.sqlite") as db:
        path = db.execute("SELECT markdown_path FROM reports WHERE report_id=1").fetchone()[0]
    assert path == str(destination / "reports" / "daily.md")


def test_storage_migration_rejects_overlapping_or_nonempty_targets(tmp_path):
    source, config, install = _source(tmp_path)
    with pytest.raises(ValueError):
        migrate_storage(config, install, source / "nested")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "my-file.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        migrate_storage(config, install, occupied)
    assert (occupied / "my-file.txt").read_text(encoding="utf-8") == "keep"


def test_report_export_creates_new_folder_and_preserves_originals(tmp_path):
    source, config, _ = _source(tmp_path)
    result = export_reports(source / "archive" / "archive.sqlite", tmp_path / "exports", "both")
    export_dir = Path(result["path"])
    assert result["reports"] == 1
    assert (export_dir / "daily-1.md").read_text(encoding="utf-8") == "# 摘要"
    assert (export_dir / "daily-1.json").is_file()
    assert (source / "reports" / "daily.md").is_file()


def test_bridge_exposes_only_methods_to_pywebview(tmp_path):
    bridge = DesktopBridge(tmp_path, tmp_path / "config.yaml", tmp_path / "app.exe", gui=object())
    assert all(name.startswith("_") for name in vars(bridge))


def test_desktop_folder_picker_runs_on_window_thread(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    class Native:
        def Invoke(self, callback):
            calls.append("ui-thread")
            callback()
        def Activate(self):
            calls.append("activated")
    class Dialog:
        def __init__(self):
            self.SelectedPath = ""
        def ShowDialog(self, owner):
            calls.append(("shown", self.SelectedPath, owner))
            self.SelectedPath = str(tmp_path)
            return 1
        def Dispose(self):
            calls.append("disposed")
    class Window:
        native = Native()
    monkeypatch.setitem(sys.modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", SimpleNamespace(
        FolderBrowserDialog=Dialog, DialogResult=SimpleNamespace(OK=1)))
    source, config, _ = _source(tmp_path)
    bridge = DesktopBridge(tmp_path, config, tmp_path / "app.exe",
                           gui=SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20)))
    bridge._window = Window()
    assert bridge.choose_folder() == str(tmp_path)
    assert calls == ["ui-thread", "activated", ("shown", str(source), bridge._window.native), "disposed"]


def test_backup_copies_message_database_and_reports_to_zip(tmp_path):
    import zipfile
    source, config, _ = _source(tmp_path)
    result = backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")
    with zipfile.ZipFile(result["path"]) as archive:
        assert "archive/archive.sqlite" in archive.namelist()
        assert archive.read("reports/daily.md").decode("utf-8") == "# 摘要"
        assert "config/config.yaml" in archive.namelist()
    assert (source / "archive" / "archive.sqlite").is_file()


def test_weekly_backup_runs_once_until_due(tmp_path):
    source, config, install = _source(tmp_path)
    bridge = DesktopBridge(install, config, install / "app.exe", gui=object(),
                           backup_root=tmp_path / "backups", temp_root=tmp_path / "cache")
    first = bridge.maybe_scheduled_backup()
    assert first is not None and Path(first["path"]).is_file()
    assert bridge.maybe_scheduled_backup() is None
    assert bridge.get_settings()["backup_schedule"] == "weekly"
    assert bridge.get_settings()["recent_backups"][0]["path"] == first["path"]
    bridge.set_backup_schedule("off")
    assert bridge.get_settings()["backup_schedule"] == "off"
    assert bridge.maybe_scheduled_backup() is None
    assert source.exists()


def test_backup_schedule_rejects_unknown_value(tmp_path):
    _, config, install = _source(tmp_path)
    bridge = DesktopBridge(install, config, install / "app.exe", gui=object(),
                           backup_root=tmp_path / "backups", temp_root=tmp_path / "cache")
    with pytest.raises(ValueError):
        bridge.set_backup_schedule("hourly")


def test_backup_file_picker_runs_on_window_thread(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    class Native:
        def Invoke(self, callback):
            calls.append("ui-thread")
            callback()
        def Activate(self):
            calls.append("activated")
    class Dialog:
        def __init__(self):
            self.FileName = ""
        def ShowDialog(self, owner):
            calls.append(("shown", owner))
            self.FileName = str(tmp_path / "backup.zip")
            return 1
        def Dispose(self):
            calls.append("disposed")
    class Window:
        native = Native()
    monkeypatch.setitem(sys.modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", SimpleNamespace(
        OpenFileDialog=Dialog, DialogResult=SimpleNamespace(OK=1)))
    _, config, install = _source(tmp_path)
    bridge = DesktopBridge(install, config, install / "app.exe", gui=object(),
                           backup_root=tmp_path / "backups", temp_root=tmp_path / "cache")
    bridge._window = Window()
    assert bridge.choose_backup_file() == str(tmp_path / "backup.zip")
    assert calls == ["ui-thread", "activated", ("shown", bridge._window.native), "disposed"]
