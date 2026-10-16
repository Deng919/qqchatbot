"""Permanent F03/F04 regressions using synthetic storage and Windows ACLs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
from datetime import datetime

import pytest
import yaml

from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
from qq_digest.desktop_settings import migrate_storage
from qq_digest.storage_files import cache_metadata, managed_temporary, safe_path
from tests.test_backup_restore import _data


def _install(tmp_path, config):
    install = tmp_path / "install"
    install.mkdir()
    launcher = install / "launcher.json"
    launcher.write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    return install, launcher


def _shared_parent(path):
    import ntsecuritycon
    import win32security as security

    path.mkdir()
    descriptor = security.GetFileSecurity(str(path), security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(
        security.ACL_REVISION,
        security.OBJECT_INHERIT_ACE | security.CONTAINER_INHERIT_ACE,
        ntsecuritycon.FILE_GENERIC_READ,
        security.CreateWellKnownSid(security.WinWorldSid),
    )
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    security.SetFileSecurity(str(path), security.DACL_SECURITY_INFORMATION, descriptor)
    return _dacl(path)


def _dacl(path):
    import win32security as security
    descriptor = security.GetFileSecurity(
        str(path), security.DACL_SECURITY_INFORMATION
    )
    text = security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        descriptor, security.SDDL_REVISION_1, security.DACL_SECURITY_INFORMATION
    )
    return hashlib.sha256(text.encode()).digest()


def _assert_private(path):
    import ntsecuritycon
    import win32api
    import win32con
    import win32security as security

    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    allowed = [user, security.CreateWellKnownSid(security.WinLocalSystemSid),
               security.CreateWellKnownSid(security.WinBuiltinAdministratorsSid)]
    acl = security.GetFileSecurity(
        str(path), security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    assert acl is not None, "secret target has an unrestricted DACL"
    for index in range(acl.GetAceCount()):
        (kind, _), mask, principal = acl.GetAce(index)
        assert not (kind == security.ACCESS_ALLOWED_ACE_TYPE
                    and mask & ntsecuritycon.FILE_READ_DATA
                    and principal not in allowed), "secret target permits a broad reader"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_backup_partial_is_private_before_first_member_and_after_publish(tmp_path, monkeypatch):
    _, config = _data(tmp_path, "source")
    shared = tmp_path / "shared-backups"
    before = _shared_parent(shared)
    original = zipfile.ZipFile.write
    observed = []

    def write(archive, *args, **kwargs):
        partial, = shared.glob("*.partial")
        _assert_private(partial)
        observed.append(partial)
        return original(archive, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    saved = Path(backup_data(config, shared, temp_root=tmp_path / "cache")["path"])
    assert observed
    _assert_private(saved)
    assert _dacl(shared) == before


def test_backup_exclusive_partial_creation_preserves_preexisting_file(tmp_path, monkeypatch):
    import qq_digest.backup_restore as module

    _, config = _data(tmp_path, "source")
    destination = tmp_path / "backups"
    destination.mkdir()
    fixed = datetime(2026, 10, 17, 12)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz)

    monkeypatch.setattr(module, "datetime", FixedDatetime)
    partial = destination / ("QQDigest-manual-" + fixed.strftime("%Y%m%d-%H%M%S-%f") + ".partial")
    partial.write_bytes(b"synthetic preexisting file")
    with pytest.raises(FileExistsError):
        backup_data(config, destination, temp_root=tmp_path / "cache")
    assert partial.read_bytes() == b"synthetic preexisting file"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_migration_secrets_inherit_private_acl_before_copy_without_changing_shared_parent(tmp_path, monkeypatch):
    import qq_digest.desktop_settings as module

    source, config = _data(tmp_path, "source")
    raw = yaml.safe_load(config.read_text("utf-8"))
    secrets = [source / "api.key", source / "credentials/profile.key",
               source / "config/secret.txt"]
    for path in secrets[:2]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-api-key", encoding="utf-8")
    external = tmp_path / "external.key"
    external.write_text("synthetic-external-key", encoding="utf-8")
    raw["ai"]["api_key_file"] = str(secrets[0])
    raw["ai"]["connections"] = [
        {"id": "local", "name": "local", "model": "test", "base_url": "https://example.com",
         "ui_api_key_file": str(secrets[1])},
        {"id": "external", "name": "external", "model": "test", "base_url": "https://example.com",
         "api_key_file": str(external)},
    ]
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    install, _ = _install(tmp_path, config)
    shared = tmp_path / "shared-storage"
    before = _shared_parent(shared)
    external_before = _dacl(external)
    destination = shared / "migrated"
    original = module.shutil.copy2
    observed = []

    def copy(old, new, *args, **kwargs):
        if Path(old) in secrets:
            _assert_private(Path(new).parent)
            observed.append(Path(new))
        return original(old, new, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "copy2", copy)
    migrate_storage(config, install, destination)
    assert len(observed) == len(secrets)
    for original_secret in secrets:
        target = destination / original_secret.relative_to(source)
        assert target.read_bytes() == original_secret.read_bytes()
        _assert_private(target)
    migrated = yaml.safe_load((destination / "config/config.yaml").read_text("utf-8"))
    assert migrated["ai"]["connections"][1]["api_key_file"] == str(external)
    assert external.read_text("utf-8") == "synthetic-external-key"
    assert _dacl(shared) == before
    assert _dacl(external) == external_before


@pytest.mark.parametrize("operation", ["backup", "migration"])
def test_privacy_failure_prevents_secret_copy_and_launcher_switch(tmp_path, monkeypatch, operation):
    import qq_digest.backup_restore as backups
    import qq_digest.desktop_settings as settings

    _, config = _data(tmp_path, "source")
    install, launcher = _install(tmp_path, config)
    original_launcher = launcher.read_bytes()
    writes = []

    def deny(*args, **kwargs):
        raise PermissionError("synthetic ACL denial")

    def copy(*args, **kwargs):
        writes.append(True)
        raise AssertionError("secret copy started before privacy succeeded")

    module = backups if operation == "backup" else settings
    monkeypatch.setattr(module, "_private_permissions", deny, raising=False)
    monkeypatch.setattr(zipfile.ZipFile, "write", copy)
    monkeypatch.setattr(module.shutil, "copy2", copy)
    with pytest.raises(PermissionError, match="synthetic ACL denial"):
        if operation == "backup":
            backup_data(config, tmp_path / "backups", temp_root=tmp_path / "cache")
        else:
            migrate_storage(config, install, tmp_path / "migrated")
    assert not writes
    assert launcher.read_bytes() == original_launcher
    assert not list(tmp_path.rglob("*.partial"))
    assert not list(tmp_path.rglob("*.zip"))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_historical_root_junction_allows_preview_and_restore_but_actual_paths_stay_protected(tmp_path, monkeypatch):
    import qq_digest.backup_restore as module

    old_root, old_config = _data(tmp_path, "historical")
    _, current_config = _data(tmp_path, "current")
    install, launcher = _install(tmp_path, current_config)
    cache = tmp_path / "cache"
    backups = tmp_path / "backups"
    saved = Path(backup_data(old_config, backups, temp_root=cache)["path"])
    old_moved = tmp_path / "moved-historical"
    # Both resolved endpoints remain in this test's exact temporary workspace.
    assert old_root.resolve().is_relative_to(tmp_path.resolve())
    assert old_moved.resolve().is_relative_to(tmp_path.resolve())
    old_root.rename(old_moved)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(old_root), str(old_moved)],
                            capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    try:
        preview = inspect_backup(saved, temp_root=cache)
        assert preview["valid"] and preview["source_data_dir"] == str(old_root)
        with pytest.raises(ValueError, match="链接|重解析点"):
            safe_path(old_root / "config/config.yaml")
        with pytest.raises(ValueError, match="链接|重解析点"):
            with managed_temporary(cache, "refresh-", old_root):
                pass
        with pytest.raises(ValueError, match="链接|重解析点"):
            inspect_backup(saved, temp_root=old_root / "cache")
        with pytest.raises(ValueError, match="链接|重解析点"):
            inspect_backup(old_root / "backup.zip", temp_root=cache)
        with pytest.raises(ValueError, match="链接|重解析点"):
            restore_backup(saved, old_root / "restored", current_config, install,
                           backup_root=backups, temp_root=cache, expected_sha256=preview["sha256"])
        destination = tmp_path / "restored"
        restored = restore_backup(saved, destination, current_config, install,
                                  backup_root=backups, temp_root=cache, expected_sha256=preview["sha256"])
        assert restored["restart_required"]
        assert json.loads(launcher.read_text("utf-8"))["config_path"] == str(destination / "config/config.yaml")
        assert (destination / "config/secret.txt").read_bytes() == (old_moved / "config/secret.txt").read_bytes()
        original_validate = module._validate_core_schema
        recognized = []

        def validate(db):
            temporary, = cache.glob("qqdigest-check-*")
            recognized.append(cache_metadata(temporary, current_config.parent.parent))
            return original_validate(db)

        monkeypatch.setattr(module, "_validate_core_schema", validate)
        current_saved = Path(backup_data(current_config, backups, temp_root=cache)["path"])
        assert inspect_backup(current_saved, temp_root=cache)["valid"]
        assert recognized
    finally:
        assert old_root.absolute().is_relative_to(tmp_path.absolute())
        old_root.rmdir()
