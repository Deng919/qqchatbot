import json
import hashlib
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage


def _data(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root = tmp_path / name
    (root / "config").mkdir(parents=True)
    (root / "knowledge").mkdir()
    (root / "reports").mkdir()
    (root / "config" / "secret.txt").write_text("local-secret-0123456789-0123456789", encoding="utf-8")
    config = root / "config" / "config.yaml"
    config.write_text(
        "security:\n  web_password_hash: test\n"
        f"  session_secret_file: '{(root / 'config' / 'secret.txt').as_posix()}'\n"
        "ai:\n  model: test\n  base_url: https://example.com\n"
        "  api_key_env: TEST_KEY\n"
        f"knowledge:\n  resource_path: '{(root / 'knowledge' / 'resources.md').as_posix()}'\n",
        encoding="utf-8",
    )
    (root / "knowledge" / "resources.md").write_text("# 资源", encoding="utf-8")
    report = root / "reports" / "daily.md"
    report.write_text("# 摘要", encoding="utf-8")
    (root / "reports" / "daily.json").write_text("{}", encoding="utf-8")
    archive = Archive.open(root / "archive" / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    stamp = datetime(2026, 9, 29, 1, tzinfo=timezone.utc)
    archive.ingest([NormalizedMessage(msg_id="m1", group_id=123, timestamp=stamp,
                                      text="hello", collected_at=stamp)])
    archive.record_report(group_id=123, report_date="2026-09-29", markdown_path=report,
                          json_path=root / "reports" / "daily.json", candidate_ids=[])
    with archive.transaction():
        cursor = archive.connection.execute(
            """INSERT INTO candidates (group_id, message_ids, created_date, candidate_type,
                 title, link, content, reason, excerpt, status, created_at, updated_at)
                 VALUES (123, '[]', '2026-09-29', 'resource', '资源', '', '', '测试', '',
                         'confirmed', '2026-09-29', '2026-09-29')"""
        )
        archive.connection.execute(
            "INSERT INTO knowledge_items VALUES (?, ?, ?, ?)",
            ("k1", cursor.lastrowid, str(root / "knowledge" / "resources.md"), "2026-09-29"),
        )
    archive.connection.close()
    return root, config


def test_backup_preview_and_restore_to_new_storage(tmp_path):
    original, old_config = _data(tmp_path, "original")
    saved, current_config = _data(tmp_path, "current")
    backup_root = tmp_path / "backups"
    backup = backup_data(old_config, backup_root, temp_root=tmp_path / "cache")
    preview = inspect_backup(Path(backup["path"]), temp_root=tmp_path / "cache")
    assert preview["valid"] is True
    assert preview["messages"] == 1
    assert preview["reports"] == 1
    assert preview["knowledge_items"] == 1
    assert preview["verified"] is True

    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(current_config)}), encoding="utf-8")
    destination = tmp_path / "restored"
    result = restore_backup(Path(backup["path"]), destination, current_config,
                            install, backup_root=backup_root,
                            temp_root=tmp_path / "cache", expected_sha256=preview["sha256"])
    assert result["restart_required"] is True
    assert Path(result["safety_backup"]).is_file()
    assert json.loads((install / "launcher.json").read_text(encoding="utf-8"))["config_path"] == str(destination / "config" / "config.yaml")
    assert (destination / "config" / "secret.txt").read_text(encoding="utf-8") == "local-secret-0123456789-0123456789"
    assert (destination / "knowledge" / "resources.md").read_text(encoding="utf-8") == "# 资源"
    assert (destination / "reports" / "daily.md").read_text(encoding="utf-8") == "# 摘要"
    with sqlite3.connect(destination / "archive" / "archive.sqlite") as db:
        assert db.execute("SELECT text FROM messages WHERE msg_id='m1'").fetchone()[0] == "hello"
        assert db.execute("SELECT markdown_path FROM reports").fetchone()[0] == str(destination / "reports" / "daily.md")
        assert db.execute("SELECT markdown_path FROM knowledge_items").fetchone()[0] == str(destination / "knowledge" / "resources.md")
    assert str(destination / "config" / "secret.txt") in (destination / "config" / "config.yaml").read_text(encoding="utf-8")
    assert saved.exists() and original.exists()


def test_backup_excludes_rebuildable_qq_snapshot_cache(tmp_path):
    root, config = _data(tmp_path, "source")
    snapshot = root / "work" / "snapshots" / "refresh-1" / "encrypted" / "nt_msg.db"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"rebuildable cache")
    backup = backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")
    with zipfile.ZipFile(backup["path"]) as archive:
        assert not any(name.startswith("work/snapshots/") for name in archive.namelist())


@pytest.mark.parametrize("mutation", ["publication", "database_only", "file_change", "file_addition", "file_removal"])
def test_backup_rejects_concurrent_source_changes(tmp_path, monkeypatch, mutation):
    import qq_digest.backup_restore as module
    root, config = _data(tmp_path, "source")
    original_copy = module.shutil.copy2
    changed = False
    def copy_with_change(source, target, *args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            if mutation in {"publication", "file_change"}:
                (root / "reports" / "daily.md").write_text("# new generation", encoding="utf-8")
            if mutation in {"publication", "database_only"}:
                with sqlite3.connect(root / "archive" / "archive.sqlite") as db:
                    db.execute("UPDATE reports SET input_fingerprint='new'")
            elif mutation == "file_addition":
                (root / "reports" / "new.md").write_text("new", encoding="utf-8")
            elif mutation == "file_removal":
                (root / "reports" / "daily.json").unlink()
        return original_copy(source, target, *args, **kwargs)
    monkeypatch.setattr(module.shutil, "copy2", copy_with_change)
    with pytest.raises((ValueError, FileNotFoundError)):
        backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")
    assert list((tmp_path / "backups").iterdir()) == []


def test_preview_rejects_tampered_content(tmp_path):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            body = b"modified" if name == "reports/daily.md" else source.read(name)
            target.writestr(name, body)
    with pytest.raises(ValueError, match="校验"):
        inspect_backup(tampered, temp_root=tmp_path / "cache")


def test_preview_rejects_malformed_manifest_with_clear_error(tmp_path):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    malformed = tmp_path / "malformed.zip"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(malformed, "w") as target:
        for name in source.namelist():
            body = json.dumps({"format_version": 1, "files": []}).encode() if name == "manifest.json" else source.read(name)
            target.writestr(name, body)
    with pytest.raises(ValueError, match="清单"):
        inspect_backup(malformed, temp_root=tmp_path / "cache")


def test_preview_rejects_oversized_manifest_before_parsing(tmp_path):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    huge = tmp_path / "huge-manifest.zip"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(huge, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, b"x" * (17 * 1024 * 1024) if name == "manifest.json" else source.read(name))
    with pytest.raises(ValueError, match="清单过大"):
        inspect_backup(huge, temp_root=tmp_path / "cache")


def test_preview_rejects_valid_sqlite_with_wrong_message_schema(tmp_path):
    root, config = _data(tmp_path, "source")
    with sqlite3.connect(root / "archive" / "archive.sqlite") as db:
        db.execute("DROP TABLE messages")
        db.execute("CREATE TABLE messages (x TEXT)")
    backup = backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")
    with pytest.raises(ValueError, match="结构"):
        inspect_backup(Path(backup["path"]), temp_root=tmp_path / "cache")


def test_preview_rejects_bad_config_even_with_updated_hash(tmp_path):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    changed = tmp_path / "bad-config.zip"
    bad_config = b"security: [invalid yaml"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(changed, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["files"]["config/config.yaml"] = {"sha256": hashlib.sha256(bad_config).hexdigest(), "size": len(bad_config)}
        for name in source.namelist():
            body = (bad_config if name == "config/config.yaml" else
                    json.dumps(manifest).encode() if name == "manifest.json" else source.read(name))
            target.writestr(name, body)
    with pytest.raises(ValueError, match="配置"):
        inspect_backup(changed, temp_root=tmp_path / "cache")


def test_restore_rejects_semantically_invalid_config_before_occupying_target(tmp_path):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    changed = tmp_path / "bad-settings.zip"
    bad_config = config.read_text(encoding="utf-8") + "\nsummary:\n  hour: 25\n"
    encoded = bad_config.encode("utf-8")
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(changed, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["files"]["config/config.yaml"] = {"sha256": hashlib.sha256(encoded).hexdigest(), "size": len(encoded)}
        for name in source.namelist():
            body = (encoded if name == "config/config.yaml" else
                    json.dumps(manifest).encode() if name == "manifest.json" else source.read(name))
            target.writestr(name, body)
    preview = inspect_backup(changed, temp_root=tmp_path / "cache")
    install = tmp_path / "install"
    install.mkdir()
    launcher = install / "launcher.json"
    launcher.write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="配置"):
        restore_backup(changed, destination, config, install, backup_root=tmp_path / "backups",
                       temp_root=tmp_path / "cache", expected_sha256=preview["sha256"])
    assert not destination.exists()
    assert json.loads(launcher.read_text(encoding="utf-8"))["config_path"] == str(config)


def test_restore_rejects_incomplete_message_schema_before_switch(tmp_path):
    root, config = _data(tmp_path, "source")
    with sqlite3.connect(root / "archive" / "archive.sqlite") as db:
        db.execute("DROP TABLE messages")
        db.execute("CREATE TABLE messages (msg_id TEXT, group_id INTEGER, timestamp TEXT, text TEXT)")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    with pytest.raises(ValueError, match="数据库结构"):
        inspect_backup(backup, temp_root=tmp_path / "cache")
    install = tmp_path / "install"
    install.mkdir()
    launcher = install / "launcher.json"
    launcher.write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="数据库结构"):
        restore_backup(backup, destination, config, install, backup_root=tmp_path / "backups",
                       temp_root=tmp_path / "cache", expected_sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
    assert not destination.exists()
    assert json.loads(launcher.read_text(encoding="utf-8"))["config_path"] == str(config)


def test_preview_rejects_message_table_without_group_message_uniqueness(tmp_path):
    root, config = _data(tmp_path, "source")
    with sqlite3.connect(root / "archive" / "archive.sqlite") as db:
        db.execute("ALTER TABLE messages RENAME TO messages_original")
        db.execute("CREATE TABLE messages AS SELECT * FROM messages_original")
        db.execute("DROP TABLE messages_original")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    with pytest.raises(ValueError, match="数据库结构"):
        inspect_backup(backup, temp_root=tmp_path / "cache")


def test_preview_rejects_path_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../escape.txt", "bad")
        archive.writestr("config/config.yaml", "test")
    with pytest.raises(ValueError, match="路径"):
        inspect_backup(bad, temp_root=tmp_path / "cache")


@pytest.mark.parametrize("unsafe_name", ["reports/a.txt:stream", "reports/CON.txt", "reports/./daily.md"])
def test_preview_rejects_windows_unsafe_names(tmp_path, unsafe_name):
    bad = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr(unsafe_name, "bad")
        archive.writestr("config/config.yaml", "test")
        archive.writestr("archive/archive.sqlite", "not-a-db")
    with pytest.raises(ValueError, match="路径"):
        inspect_backup(bad, temp_root=tmp_path / "cache")


def test_restore_rejects_changed_backup_and_keeps_launcher(tmp_path):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    install = tmp_path / "install"
    install.mkdir()
    launcher = install / "launcher.json"
    launcher.write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    with pytest.raises(ValueError, match="已变化"):
        restore_backup(backup, tmp_path / "restored", config, install,
                       backup_root=tmp_path / "backups", temp_root=tmp_path / "cache",
                       expected_sha256="not-the-preview-hash")
    assert json.loads(launcher.read_text(encoding="utf-8"))["config_path"] == str(config)
    assert not (tmp_path / "restored").exists()


def test_legacy_backup_recreates_missing_local_session_secret(tmp_path):
    from qq_digest.config import load_config

    _, config = _data(tmp_path, "source")
    modern = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    legacy = tmp_path / "backups" / "legacy.zip"
    with zipfile.ZipFile(modern) as original, zipfile.ZipFile(legacy, "w") as old:
        for name in original.namelist():
            if name not in {"manifest.json", "config/secret.txt"}:
                old.writestr(name, original.read(name))
    preview = inspect_backup(legacy, temp_root=tmp_path / "cache")
    assert preview["legacy"] is True
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    destination = tmp_path / "restored"
    restore_backup(legacy, destination, config, install, backup_root=tmp_path / "backups",
                   temp_root=tmp_path / "cache", expected_sha256=preview["sha256"])
    assert len(load_config(destination / "config" / "config.yaml").resolve_session_secret()) >= 32


def test_legacy_message_only_backup_rebases_config_paths(tmp_path):
    root, config = _data(tmp_path, "source")
    with sqlite3.connect(root / "archive" / "archive.sqlite") as db:
        db.execute("DELETE FROM reports")
        db.execute("DELETE FROM knowledge_items")
    modern = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    legacy = tmp_path / "backups" / "message-only.zip"
    with zipfile.ZipFile(modern) as source, zipfile.ZipFile(legacy, "w") as old:
        for name in source.namelist():
            if name not in {"manifest.json", "config/secret.txt"}:
                old.writestr(name, source.read(name))
    preview = inspect_backup(legacy, temp_root=tmp_path / "cache")
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    destination = tmp_path / "restored"
    restore_backup(legacy, destination, config, install, backup_root=tmp_path / "backups",
                   temp_root=tmp_path / "cache", expected_sha256=preview["sha256"])
    restored_yaml = (destination / "config" / "config.yaml").read_text(encoding="utf-8")
    assert str(destination / "config" / "secret.txt") in restored_yaml
    assert str(root / "config" / "secret.txt") not in restored_yaml


def test_restore_rechecks_backup_before_switching_launcher(tmp_path, monkeypatch):
    import qq_digest.backup_restore as module

    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    fingerprint = inspect_backup(backup, temp_root=tmp_path / "cache")["sha256"]
    install = tmp_path / "install"
    install.mkdir()
    launcher = install / "launcher.json"
    launcher.write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    original_backup = module.backup_data

    def change_after_safety_copy(*args, **kwargs):
        result = original_backup(*args, **kwargs)
        with backup.open("ab") as file:
            file.write(b"changed")
        return result

    monkeypatch.setattr(module, "backup_data", change_after_safety_copy)
    with pytest.raises(ValueError, match="已变化"):
        restore_backup(backup, tmp_path / "restored", config, install,
                       backup_root=tmp_path / "backups", temp_root=tmp_path / "cache",
                       expected_sha256=fingerprint)
    assert json.loads(launcher.read_text(encoding="utf-8"))["config_path"] == str(config)


def test_backup_stays_verifiable_if_live_report_changes_while_zipping(tmp_path, monkeypatch):
    _, config = _data(tmp_path, "source")
    live_report = config.parent.parent / "reports" / "daily.md"
    original_write = zipfile.ZipFile.write

    def change_live_report(self, filename, arcname=None, *args, **kwargs):
        if arcname == "reports/daily.md":
            live_report.write_text("changed during backup", encoding="utf-8")
        return original_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", change_live_report)
    backup = backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")
    assert inspect_backup(Path(backup["path"]), temp_root=tmp_path / "cache")["valid"]


def test_restored_real_archive_can_start_and_search(tmp_path):
    from datetime import datetime, timezone

    from qq_digest.archive import Archive
    from qq_digest.models import GroupConfig, NormalizedMessage
    from qq_digest.search import search_archive

    root = tmp_path / "data"
    (root / "config").mkdir(parents=True)
    config = root / "config" / "config.yaml"
    config.write_text("security:\n  web_password_hash: test\nai:\n  model: test\n  base_url: https://example.com\n  api_key_env: TEST_KEY\n", encoding="utf-8")
    (root / "reports").mkdir()
    report = root / "reports" / "daily.md"
    report.write_text("# 早会摘要", encoding="utf-8")
    report_json = root / "reports" / "daily.json"
    report_json.write_text("{}", encoding="utf-8")
    archive = Archive.open(root / "archive" / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="项目群")])
    stamp = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    archive.ingest([NormalizedMessage(msg_id="m1", group_id=123, sender_qq=1001,
                                      timestamp=stamp, text="早会进度正常", collected_at=stamp)])
    archive.record_report(group_id=123, report_date="2026-09-29", markdown_path=report,
                          json_path=report_json, candidate_ids=[])
    archive.connection.close()
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")["path"])
    preview = inspect_backup(backup, temp_root=tmp_path / "cache")
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    restored = tmp_path / "restored"
    restore_backup(backup, restored, config, install, backup_root=tmp_path / "backups",
                   temp_root=tmp_path / "cache", expected_sha256=preview["sha256"])
    reopened = Archive.open(restored / "archive" / "archive.sqlite")
    try:
        found = search_archive(reopened, query="早会", kind="message")
        assert found["total"] == 1
        assert (restored / "reports" / "daily.md").read_text(encoding="utf-8") == "# 早会摘要"
    finally:
        reopened.connection.close()


def test_incomplete_backup_is_not_visible_as_finished_zip(tmp_path, monkeypatch):
    _, config = _data(tmp_path, "source")
    destination = tmp_path / "backups"
    original_write = zipfile.ZipFile.write
    seen_during_write = []

    def observe_write(self, filename, arcname=None, *args, **kwargs):
        seen_during_write.append(list(destination.glob("QQDigest-*.zip")))
        return original_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", observe_write)
    result = backup_data(config, destination, temp_root=tmp_path / "cache")
    assert seen_during_write and all(not files for files in seen_during_write)
    assert Path(result["path"]).is_file()
