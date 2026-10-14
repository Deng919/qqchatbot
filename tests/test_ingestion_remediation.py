"""Synthetic regression coverage for the 2026-10-14 ingestion audit."""
import sqlite3
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from qq_digest.archive import Archive
from qq_digest.collector.protobuf import extract_message_text, make_bytes_field, make_varint_field
from qq_digest.config import AIConfig, Config, NTQQConfig, SecurityConfig
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.sync import run_message_sync
from tests.test_bot_commands import build_handler
import qq_digest.cli as cli
import qq_digest.refresh as refresh_module


NOW = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def config_for(tmp_path):
    return Config(data_dir=tmp_path, archive_path=tmp_path / "archive.sqlite",
                  report_dir=tmp_path / "reports", knowledge_dir=tmp_path / "knowledge",
                  work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
                  security=SecurityConfig(web_password_hash="unused"),
                  ai=AIConfig(base_url="https://example.com", model="unused", api_key_env="TEST_KEY"),
                  ntqq=NTQQConfig(enabled=True, db_dir=str(tmp_path / "source")))


class WindowCollector:
    def __init__(self, messages):
        self.messages, self.windows = messages, []

    def collect(self, group_id, start, end):
        self.windows.append((group_id, start, end))
        return [message for message in self.messages
                if message.group_id == group_id and start <= message.timestamp < end]


def test_cli_sync_retains_history_after_week_offline_and_selected_group(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="selected"), GroupConfig(group_id=456, name="other")])
    previous = NOW - timedelta(days=7)
    archive.mark_sync(group_id=123, last_timestamp=previous)
    archive.mark_sync(group_id=456, last_timestamp=previous)
    archive.close()
    collector = WindowCollector([NormalizedMessage(msg_id="missed-history", group_id=123, sender_qq=1,
                                timestamp=NOW-timedelta(days=3), text="history", collected_at=NOW)])
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "NTQQCollector", lambda **_: collector)
    result = CliRunner().invoke(cli.app, ["sync", "--group-id", "123"])
    assert result.exit_code == 0, result.output
    assert run_message_sync(config=config, now=NOW, refresh=False, collector=collector).success
    archive = Archive.open(config.archive_path)
    try:
        assert archive.count_messages(123) == 1
    finally:
        archive.close()
    assert collector.windows[0][1] == previous - timedelta(minutes=config.collection.overlap_minutes)
    # CLI operated only the requested group; the periodic call later catches the other group.
    assert [window[0] for window in collector.windows] == [123, 123, 456]


@pytest.mark.parametrize("previous_days", [None, 30])
def test_cli_backfill_does_not_move_full_history_cursor(tmp_path, monkeypatch, previous_days):
    config = config_for(tmp_path)
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="history")])
    if previous_days:
        archive.mark_sync(group_id=123, last_timestamp=NOW-timedelta(days=previous_days))
    before = archive.connection.execute("SELECT last_timestamp FROM sync_state WHERE group_id=123").fetchone()
    expected = before[0] if before else None
    archive.close()
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "NTQQCollector", lambda **_: WindowCollector([]))
    result = CliRunner().invoke(cli.app, ["backfill", "--days", "2"])
    assert result.exit_code == 0, result.output
    archive = Archive.open(config.archive_path)
    try:
        state = archive.connection.execute("SELECT last_timestamp,last_success_at FROM sync_state WHERE group_id=123").fetchone()
        assert state[0] == expected
        assert state[1] is not None
    finally:
        archive.close()


@pytest.mark.parametrize("existing", [True, False])
def test_refresh_install_failure_restores_whole_previous_set(tmp_path, monkeypatch, existing):
    output, staged = tmp_path / "output", tmp_path / "staged"
    output.mkdir(); staged.mkdir()
    names = ("group_info.db", "nt_msg.db", "profile_info.db")
    for name in names:
        if existing:
            (output / name).write_bytes(("old-" + name).encode())
        (staged / name).write_bytes(("new-" + name).encode())
    original_replace = refresh_module.os.replace
    installs = 0
    def fail_second_install(source, target):
        nonlocal installs
        if Path(source).parent == staged and Path(target).parent == output:
            installs += 1
            if installs == 2:
                raise PermissionError("locked second database")
        return original_replace(source, target)
    monkeypatch.setattr(refresh_module.os, "replace", fail_second_install)
    with pytest.raises(PermissionError, match="locked second"):
        refresh_module._publish_staged_outputs(staged, output, refreshed_names=set(names), required_files=set(names[:2]))
    for name in names:
        if existing:
            assert (output / name).read_bytes() == ("old-" + name).encode()
        else:
            assert not (output / name).exists()


def test_refresh_real_windows_target_lock_preserves_old_set(tmp_path):
    output, staged = tmp_path / "output", tmp_path / "staged"
    output.mkdir(); staged.mkdir()
    for name in ("group_info.db", "nt_msg.db"):
        (output / name).write_bytes(b"old")
        (staged / name).write_bytes(b"new")
    paths = list(staged.iterdir())
    locked = output / paths[1].name
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(locked), 0x80000000, 1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(PermissionError):
            refresh_module._publish_staged_outputs(staged, output,
                refreshed_names={path.name for path in paths}, required_files={path.name for path in paths})
    finally:
        api.CloseHandle(handle)
    assert all((output / path.name).read_bytes() == b"old" for path in paths)
    assert sorted(path.name for path in output.iterdir()) == sorted(path.name for path in paths)


@pytest.mark.parametrize("command", ["later", "ignore"])
def test_bot_cannot_hide_confirmed_knowledge(tmp_path, command):
    archive, candidates, ids, handler = build_handler(tmp_path)
    try:
        assert handler.handle_message("owner", f"/confirm {ids[0]}").success
        path = handler.knowledge.path_for("resource")
        before = path.read_bytes()
        result = handler.handle_message("owner", f"/{command} {ids[0]}")
        assert not result.success
        assert candidates.get(ids[0]).status == "confirmed"
        assert archive.connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 1
        assert path.read_bytes() == before
    finally:
        archive.close()


def test_bot_confirmation_keeps_configured_group_name_without_archive_group(tmp_path):
    archive, candidates, ids, handler = build_handler(tmp_path)
    try:
        assert not archive.all_groups()
        result = handler.handle_message("owner", f"/confirm {ids[0]}")
        assert result.success, result.message
        markdown = handler.knowledge.path_for("resource").read_text(encoding="utf-8")
        assert "- 来源：开发群" in markdown
        assert candidates.get(ids[0]).status == "confirmed"
    finally:
        archive.close()


@pytest.mark.parametrize("command,expected", [("later", "later"), ("ignore", "ignored")])
def test_bot_review_change_increments_candidate_revision(tmp_path, command, expected):
    archive, candidates, ids, handler = build_handler(tmp_path)
    try:
        before = candidates.get(ids[0]).revision
        result = handler.handle_message("owner", f"/{command} {ids[0]}")
        assert result.success
        current = candidates.get(ids[0])
        assert current.status == expected
        assert current.revision == before + 1
    finally:
        archive.close()


@pytest.mark.parametrize("command", ["later", "ignore"])
def test_bot_review_state_change_cannot_overwrite_concurrent_confirmation(tmp_path, monkeypatch, command):
    from qq_digest.knowledge_library import KnowledgeLibrary
    archive, candidates, ids, handler = build_handler(tmp_path)
    original_get = candidates.get
    def confirm_after_read(candidate_id):
        stale = original_get(candidate_id)
        KnowledgeLibrary(archive, handler.knowledge).confirm_candidate(candidate_id)
        return stale
    monkeypatch.setattr(candidates, "get", confirm_after_read)
    try:
        result = handler.handle_message("owner", f"/{command} {ids[0]}")
        assert not result.success
        assert original_get(ids[0]).status == "confirmed"
        assert archive.connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 1
    finally:
        archive.close()


@pytest.mark.parametrize("existing", [True, False])
def test_bot_database_failure_rolls_back_markdown_and_candidate(tmp_path, existing):
    archive, candidates, ids, handler = build_handler(tmp_path)
    path = handler.knowledge.path_for("resource")
    if existing:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"existing knowledge\n")
    original = path.read_bytes() if existing else None
    archive.connection.execute("CREATE TRIGGER reject_knowledge BEFORE INSERT ON knowledge_items BEGIN SELECT RAISE(FAIL, 'metadata unavailable'); END")
    archive.connection.commit()
    try:
        result = handler.handle_message("owner", f"/confirm {ids[0]}")
        assert not result.success
        assert candidates.get(ids[0]).status == "pending"
        assert archive.connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0
        assert (path.read_bytes() if path.exists() else None) == original
    finally:
        archive.close()


@pytest.mark.parametrize("elements,expected,kind", [
    ([(1, "first"), (1, "second")], "firstsecond", "text"),
    ([(2, ""), (1, "caption"), (3, "")], "[图片]caption[文件]", "text"),
    ([(99, ""), (1, "supported")], "supported", "text"),
])
def test_protobuf_preserves_all_repeated_elements(elements, expected, kind):
    blob = b"".join(make_bytes_field(40800, make_varint_field(45002, item_type)
                       + make_bytes_field(45101, text.encode())) for item_type, text in elements)
    assert extract_message_text(blob) == (expected, kind)


def create_existing_source(source, *, fts=False):
    source.mkdir()
    with sqlite3.connect(source / "group_info.db") as db:
        db.execute('CREATE TABLE group_list ("60001" INTEGER, "60007" TEXT)')
        db.execute("INSERT INTO group_list VALUES (123, 'existing')")
    with sqlite3.connect(source / "nt_msg.db") as db:
        db.execute('CREATE TABLE group_msg_table ("40001" INTEGER, "40020" TEXT, "40021" TEXT, "40027" INTEGER, "40050" INTEGER, "40800" BLOB)')
        blob = make_bytes_field(40800, make_varint_field(45002, 1) + make_bytes_field(45101, b"existing body"))
        db.execute('INSERT INTO group_msg_table VALUES (1, \'\', \'123\', 456, ?, ?)', (int((NOW-timedelta(hours=1)).timestamp()), blob))
    if fts:
        with sqlite3.connect(source / "group_msg_fts.db") as db:
            db.execute('CREATE TABLE group_msg_fts ("40001" INTEGER, "40050" INTEGER, '
                       '"40020" TEXT, "40021" TEXT, "40027" INTEGER, "41701" TEXT, '
                       '"41702" TEXT, "41703" TEXT, "41704" TEXT)')
            db.execute('INSERT INTO group_msg_fts VALUES (1, ?, \'456\', \'123\', 123, '
                       '\'existing body\', \'\', \'2\', \'1\')',
                       (int((NOW-timedelta(hours=1)).timestamp()),))


@pytest.mark.parametrize("fts", [False, True])
def test_refresh_without_account_reads_existing_valid_databases_without_qq(tmp_path, monkeypatch, fts):
    source = tmp_path / "existing"
    create_existing_source(source, fts=fts)
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    result = refresh_module.refresh_database(qq_number=0, output_dir=source)
    assert result.success, result.message
    assert result.files_decrypted == 0
    assert before == {path.name: path.read_bytes() for path in source.iterdir()}


@pytest.mark.parametrize("fault", ["missing", "wrong_schema", "corrupt", "invalid_preferred_fts"])
def test_refresh_without_account_rejects_unreadable_existing_source(tmp_path, monkeypatch, fault):
    source = tmp_path / "existing"
    if fault != "missing":
        create_existing_source(source)
        if fault == "wrong_schema":
            with sqlite3.connect(source / "nt_msg.db") as db:
                db.execute("DROP TABLE group_msg_table")
        elif fault == "corrupt":
            (source / "nt_msg.db").write_bytes(b"broken database")
        else:
            with sqlite3.connect(source / "group_msg_fts.db") as db:
                db.execute("CREATE TABLE unrelated (a)")
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    result = refresh_module.refresh_database(qq_number=0, output_dir=source)
    assert not result.success
    assert "已有" in result.message
    if fault == "missing":
        assert not source.exists()


@pytest.mark.parametrize("name,table,fts", [
    ("group_info.db", "group_list", False),
    ("nt_msg.db", "group_msg_table", False),
    ("group_msg_fts.db", "group_msg_fts", True),
])
def test_existing_source_incompatible_columns_fail_before_sync_cursor_advances(tmp_path, monkeypatch, name, table, fts):
    config = config_for(tmp_path)
    source = Path(config.ntqq.db_dir)
    create_existing_source(source, fts=fts)
    with sqlite3.connect(source / name) as db:
        db.execute(f'DROP TABLE "{table}"')
        db.execute(f'CREATE TABLE "{table}" (text TEXT)')
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="existing")])
    previous = NOW-timedelta(days=7)
    archive.mark_sync(group_id=123, last_timestamp=previous)
    original_cursor = archive.connection.execute("SELECT last_timestamp FROM sync_state WHERE group_id=123").fetchone()[0]
    archive.close()
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    refresh = refresh_module.refresh_database(qq_number=0, output_dir=source)
    assert not refresh.success, refresh.message
    assert name in refresh.message
    sync = run_message_sync(config=config, now=NOW)
    assert not sync.success, sync.errors
    archive = Archive.open(config.archive_path)
    try:
        assert archive.count_messages(123) == 0
        assert archive.connection.execute("SELECT last_timestamp FROM sync_state WHERE group_id=123").fetchone()[0] == original_cursor
    finally:
        archive.close()


@pytest.mark.parametrize("fts", [False, True])
def test_regular_sync_without_account_uses_existing_source(tmp_path, monkeypatch, fts):
    config = config_for(tmp_path)
    create_existing_source(Path(config.ntqq.db_dir), fts=fts)
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="existing")])
    archive.close()
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    result = run_message_sync(config=config, now=NOW)
    assert result.success, result.errors
    assert result.messages_inserted == 1


def test_existing_legacy_source_accepts_collector_supported_sender_column(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    source = Path(config.ntqq.db_dir)
    create_existing_source(source)
    with sqlite3.connect(source / "nt_msg.db") as db:
        db.execute('ALTER TABLE group_msg_table RENAME COLUMN "40027" TO "40033"')
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="existing")])
    archive.close()
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    result = run_message_sync(config=config, now=NOW)
    assert result.success, result.errors
    assert result.messages_inserted == 1


def test_history_inspection_refresh_without_account_reads_existing_source(tmp_path, monkeypatch):
    from qq_digest.history_inspection import run_history_inspection
    config = config_for(tmp_path)
    create_existing_source(Path(config.ntqq.db_dir))
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="existing")])
    archive.close()
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    result = run_history_inspection(config=config, refresh=True, now=NOW+timedelta(days=2))
    assert result["status"] == "success", result["errors"]
    assert result["missing_count"] == 1


def test_daily_task_refresh_without_account_reads_existing_source(tmp_path, monkeypatch):
    import qq_digest.application_runner as runner
    config = config_for(tmp_path)
    create_existing_source(Path(config.ntqq.db_dir))
    archive = Archive.open(config.archive_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="existing")])
    archive.close()
    class FakeAI:
        def chat(self, messages):
            return {"group_id": 123, "main_topics": [], "conclusions": [], "resources": [],
                    "tasks": [], "open_questions": [], "candidates": []}
        def close(self):
            pass
    monkeypatch.setattr(runner.ai_factory, "build_ai_client", lambda _: FakeAI())
    monkeypatch.setattr(refresh_module, "_find_qq_main_pid", lambda: None)
    result = runner.run_daily_task(config, scheduled_at=NOW)
    assert result["success"], result
    assert result["messages_inserted"] == 1
    assert result["reports"] == 1
