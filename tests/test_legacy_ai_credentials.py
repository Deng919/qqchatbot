import json
from pathlib import Path
import zipfile

import pytest
import yaml

from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
from qq_digest.config import load_config
from qq_digest.desktop_settings import migrate_storage
from qq_digest.storage_management import StorageManager
from tests.test_backup_restore import _data
from tests.test_storage_management import NOW, abandoned_cache


def retire(path):
    from qq_digest.first_use import ConfigFileStore
    cfg=load_config(path)
    store=ConfigFileStore(cfg,path)
    store.save(store.revision,{'ai':{'base_url':'http://127.0.0.1:62406/v1','model':'gpt-6.1-sol'}},{})
    raw=yaml.safe_load(path.read_text('utf-8'))
    assert 'connections' not in raw['ai']
    assert 'purpose_connections' not in raw['ai']


KEY_FIELDS = ("api_key_file", "ui_api_key_file")


def _profiles(tmp_path, key_field):
    root, config_path = _data(tmp_path, "source")
    paths = {
        "managed": root / "config" / "secrets" / "profile.key",
        "custom": root / "credentials" / "profile.key",
        "external": tmp_path / "external" / "profile.key",
    }
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["ai"]["connections"] = []
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture-{name}", encoding="utf-8")
        raw["ai"]["connections"].append({
            "id": name, "name": name, "base_url": "https://example.com/v1",
            "model": "test", key_field: str(path),
        })
    raw["ai"]["purpose_connections"] = {"summary": "managed", "report_qa": "custom"}
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(config_path)}), encoding="utf-8")
    return root, config_path, paths, install


def _assert_profile_paths(config_path, original_root, destination, paths, key_field):
    restored = load_config(config_path, create_dirs=False)
    expected_paths=set()
    for name,original in paths.items():
        expected=destination / original.relative_to(original_root) if name!='external' else original
        expected_paths.add(expected)
        assert expected.read_text(encoding='utf-8')==f'fixture-{name}'
    assert {Path(value) for value in restored.retired_ai_key_files.values()}==expected_paths
    assert 'connections' not in restored.ai.model_dump()
    assert paths["external"].read_text(encoding="utf-8") == "fixture-external"
    assert not (destination / "external").exists()


@pytest.mark.parametrize("key_field", KEY_FIELDS)
def test_backup_restore_preserves_retired_credentials_and_external_identity(tmp_path, key_field):
    root, config, paths, install = _profiles(tmp_path, key_field)
    retire(config)
    backups, cache = tmp_path / "backups", tmp_path / "cache"
    saved = Path(backup_data(config, backups, temp_root=cache)["path"])
    with zipfile.ZipFile(saved) as archive:
        assert "config/secrets/profile.key" in archive.namelist()
        assert "credentials/profile.key" in archive.namelist()
        assert not any(name.startswith("external/") for name in archive.namelist())
    preview = inspect_backup(saved, temp_root=cache)
    destination = tmp_path / "restored"
    restore_backup(saved, destination, config, install, backup_root=backups,
                   temp_root=cache, expected_sha256=preview["sha256"])
    _assert_profile_paths(destination / "config" / "config.yaml", root, destination, paths, key_field)


@pytest.mark.parametrize("key_field", KEY_FIELDS)
def test_storage_migration_rebases_and_copies_retired_credentials(tmp_path, key_field):
    root, config, paths, install = _profiles(tmp_path, key_field)
    retire(config)
    destination = tmp_path / "migrated"
    migrate_storage(config, install, destination)
    _assert_profile_paths(destination / "config" / "config.yaml", root, destination, paths, key_field)
    assert all(path.is_file() for path in paths.values())


@pytest.mark.parametrize("key_field", KEY_FIELDS)
def test_cleanup_preserves_cache_containing_legacy_credential(tmp_path, key_field):
    _, config = _data(tmp_path, "source")
    cache = tmp_path / "cache"
    cache.mkdir()
    service = StorageManager(load_config(config), tmp_path / "backups", cache, now=lambda: NOW)
    protected = abandoned_cache(service, 8)
    profile_key = protected / "profile.key"
    profile_key.write_text("fixture-profile", encoding="utf-8")
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["ai"]["connections"] = [{"id": "saved", "name": "saved",
        "base_url": "https://example.com/v1", "model": "test", key_field: str(profile_key)}]
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    service.config = load_config(config)
    preview = service.preview()
    assert not preview["candidates"]
    assert str(protected) in {item["path"] for item in preview["protected"]}
    assert service.clean(preview["token"])["deleted_count"] == 0
    assert profile_key.read_text(encoding="utf-8") == "fixture-profile"
    retire(config)
    service.config=load_config(config)
    after=service.preview()
    assert not after['candidates']
    assert service.clean(after['token'])['deleted_count']==0
    assert profile_key.is_file()


def test_profile_custom_member_does_not_allow_unrelated_archive_files(tmp_path):
    _, config, _, _ = _profiles(tmp_path, "api_key_file")
    cache = tmp_path / "cache"
    saved = Path(backup_data(config, tmp_path / "backups", temp_root=cache)["path"])
    with zipfile.ZipFile(saved, "a") as archive:
        archive.writestr("credentials/unrelated.key", "unrelated")
    with pytest.raises(ValueError, match="不安全"):
        inspect_backup(saved, temp_root=cache)

def test_version_manager_protects_legacy_credential_paths(tmp_path, monkeypatch):
    import qq_digest.desktop_settings as module
    import qq_digest.desktop_updates as updates
    from qq_digest.desktop_settings import DesktopBridge
    from tests.test_desktop_updates import env
    registry,records,_,_,_=env(tmp_path)
    _,config,paths,_=_profiles(tmp_path,'ui_api_key_file')
    install=Path(records[0]['path'])
    (install/'launcher.json').write_text(json.dumps({'config_path':str(config)}),'utf-8')
    monkeypatch.setattr(module,'release_root',lambda:registry.root)
    monkeypatch.setattr(updates,'STATE_DIR',registry.state_dir)
    bridge=DesktopBridge(install,config,install/'QQDigestDesktop.exe',gui=None,backup_root=tmp_path/'backups',temp_root=tmp_path/'cache')
    protected=[Path(value) for value in bridge._updates().data_paths]
    assert all(path in protected for path in paths.values())
    retire(config)
    protected=[Path(value) for value in bridge._updates().data_paths]
    assert all(path in protected for path in paths.values())
