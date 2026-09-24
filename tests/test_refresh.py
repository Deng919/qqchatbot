from pathlib import Path

import sqlite3
import struct

import pytest

import qq_digest.refresh as refresh_module
from qq_digest.refresh import EXT_HEADER, PAGE_SIZE, _decrypt_db


def test_stable_snapshot_retries_when_source_changes(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.db"
    target = tmp_path / "snapshot.db"
    source.write_bytes(b"first")
    attempts = 0

    def changing_copy(src, dst):
        nonlocal attempts
        attempts += 1
        Path(dst).write_bytes(Path(src).read_bytes())
        if attempts == 1:
            Path(src).write_bytes(b"second-version")

    monkeypatch.setattr(refresh_module.shutil, "copy2", changing_copy)

    refresh_module._copy_stable_snapshot(source, target, retries=2, retry_delay=0)

    assert attempts == 2
    assert target.read_bytes() == b"second-version"


def test_stable_snapshot_raises_after_repeated_source_changes(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.db"
    target = tmp_path / "snapshot.db"
    source.write_bytes(b"0")

    def changing_copy(src, dst):
        value = int(Path(src).read_text()) + 1
        Path(dst).write_text(str(value))
        Path(src).write_text(str(value))

    monkeypatch.setattr(refresh_module.shutil, "copy2", changing_copy)

    with pytest.raises(refresh_module.SnapshotError):
        refresh_module._copy_stable_snapshot(source, target, retries=2, retry_delay=0)

    assert not target.exists()


def test_database_snapshot_copies_matching_wal_set(tmp_path: Path):
    source = tmp_path / "source.db"
    source_wal = tmp_path / "source.db-wal"
    target = tmp_path / "snapshot.db"
    source.write_bytes(b"database")
    source_wal.write_bytes(b"wal-data")

    refresh_module._copy_database_snapshot(source, target, retry_delay=0)

    assert target.read_bytes() == b"database"
    assert Path(str(target) + "-wal").read_bytes() == b"wal-data"


def test_encrypted_wal_applies_only_through_last_commit(tmp_path: Path, monkeypatch):
    output = tmp_path / "plain.db"
    output.write_bytes(b"A" * PAGE_SIZE + b"B" * PAGE_SIZE)
    wal = tmp_path / "encrypted.db-wal"
    header = struct.pack(">8I", 0x377F0682, 3007000, PAGE_SIZE, 0, 1, 2, 0, 0)

    def frame(page_number: int, commit_size: int, payload: bytes) -> bytes:
        return struct.pack(">6I", page_number, commit_size, 1, 2, 0, 0) + payload

    committed = b"C" * PAGE_SIZE
    uncommitted = b"D" * PAGE_SIZE
    wal.write_bytes(
        header
        + frame(2, 2, committed)
        + frame(1, 0, uncommitted)
    )
    monkeypatch.setattr(
        refresh_module,
        "_decrypt_page_image",
        lambda payload, key, page_number: payload,
    )

    applied = refresh_module._apply_encrypted_wal(wal, output, b"0" * 32)

    assert applied == 1
    assert output.read_bytes()[:PAGE_SIZE] == b"A" * PAGE_SIZE
    assert output.read_bytes()[PAGE_SIZE:] == committed


def test_failed_decryption_preserves_existing_output(tmp_path: Path):
    source = tmp_path / "encrypted.db"
    output = tmp_path / "plain.db"
    source.write_bytes(b"x" * (EXT_HEADER + PAGE_SIZE * 2))
    output.write_bytes(b"existing database")

    result = _decrypt_db(source, output, b"0" * 32)

    assert result is False
    assert output.read_bytes() == b"existing database"


def test_successful_decryption_replaces_output(tmp_path: Path, monkeypatch):
    plain_path = tmp_path / "plain-source.db"
    conn = sqlite3.connect(plain_path)
    conn.execute("PRAGMA page_size=4096")
    conn.execute("PRAGMA reserved_bytes=48")
    conn.execute("CREATE TABLE x(a)")
    conn.commit()
    conn.close()
    plain = plain_path.read_bytes()
    assert len(plain) == PAGE_SIZE * 2

    source = tmp_path / "encrypted.db"
    source.write_bytes(b"x" * EXT_HEADER + b"x" * PAGE_SIZE * 2)
    output = tmp_path / "plain.db"
    output.write_bytes(b"old database")

    calls = []

    def fake_decrypt(page_data: bytes, enc_key: bytes, skip_salt: int = 0) -> bytes:
        calls.append(skip_salt)
        if len(calls) == 1:
            page = bytearray(plain[:PAGE_SIZE])
            page[20] = 80
        elif len(calls) == 2:
            page = plain[PAGE_SIZE:]
        else:
            raise AssertionError("unexpected extra page")
        if skip_salt:
            return page[skip_salt : PAGE_SIZE - 48]
        return page[: PAGE_SIZE - 48]

    monkeypatch.setattr(refresh_module, "_decrypt_page", fake_decrypt)

    class FakeConnection:
        def execute(self, query: str):
            assert "sqlite_master" in query

            class FakeCursor:
                def fetchall(self):
                    return [("x",)]

            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(refresh_module.sqlite3, "connect", lambda _: FakeConnection())
    result = _decrypt_db(source, output, b"0" * 32)

    assert result is True
    data = output.read_bytes()
    assert data.startswith(b"SQLite format 3\x00")
    assert data[20] == 80
    assert len(data) == PAGE_SIZE * 2


def test_decryption_requires_expected_tables(tmp_path: Path, monkeypatch):
    source = tmp_path / "encrypted.db"
    source.write_bytes(b"x" * (EXT_HEADER + PAGE_SIZE))
    output = tmp_path / "plain.db"
    output.write_bytes(b"old database")

    monkeypatch.setattr(
        refresh_module,
        "_decrypt_page",
        lambda page_data, enc_key, skip_salt=0: b"x" * (PAGE_SIZE - 48 - skip_salt),
    )

    class FakeConnection:
        def execute(self, query: str):
            class FakeCursor:
                def fetchall(self):
                    return [("unrelated_table",)]

            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(refresh_module.sqlite3, "connect", lambda _: FakeConnection())

    result = _decrypt_db(
        source,
        output,
        b"0" * 32,
        required_tables={"group_msg_table"},
    )

    assert result is False
    assert output.read_bytes() == b"old database"


def test_integrity_failure_preserves_existing_output(tmp_path: Path, monkeypatch):
    source = tmp_path / "encrypted.db"
    source.write_bytes(b"x" * (EXT_HEADER + PAGE_SIZE))
    output = tmp_path / "plain.db"
    output.write_bytes(b"old database")

    monkeypatch.setattr(
        refresh_module,
        "_decrypt_page",
        lambda page_data, enc_key, skip_salt=0: b"x" * (PAGE_SIZE - 48 - skip_salt),
    )

    class FakeConnection:
        def execute(self, query: str):
            class FakeCursor:
                def fetchall(self):
                    if "sqlite_master" in query:
                        return [("group_msg_fts",)]
                    return [("database disk image is malformed",)]

            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(refresh_module.sqlite3, "connect", lambda _: FakeConnection())

    result = _decrypt_db(
        source,
        output,
        b"0" * 32,
        required_tables={"group_msg_fts"},
        check_integrity=True,
    )

    assert result is False
    assert output.read_bytes() == b"old database"


def test_core_outputs_publish_as_one_validated_set(tmp_path: Path):
    output = tmp_path / "output"
    staged = tmp_path / "staged"
    output.mkdir()
    staged.mkdir()
    for name in ("nt_msg.db", "group_info.db", "group_msg_fts.db"):
        (output / name).write_text(f"old-{name}")
        (staged / name).write_text(f"new-{name}")

    missing = refresh_module._publish_staged_outputs(
        staged,
        output,
        refreshed_names={"nt_msg.db", "group_info.db"},
        required_files={"nt_msg.db", "group_info.db", "group_msg_fts.db"},
    )

    assert missing == ["group_msg_fts.db"]
    assert (output / "nt_msg.db").read_text() == "old-nt_msg.db"
    assert (output / "group_info.db").read_text() == "old-group_info.db"

    missing = refresh_module._publish_staged_outputs(
        staged,
        output,
        refreshed_names={"nt_msg.db", "group_info.db", "group_msg_fts.db"},
        required_files={"nt_msg.db", "group_info.db", "group_msg_fts.db"},
    )

    assert missing == []
    assert (output / "nt_msg.db").read_text() == "new-nt_msg.db"
    assert (output / "group_msg_fts.db").read_text() == "new-group_msg_fts.db"


def test_required_refresh_files_follow_active_collector_path():
    assert refresh_module._required_refresh_files(
        {"nt_msg.db", "group_info.db", "group_msg_fts.db"}
    ) == {"group_info.db", "group_msg_fts.db"}
    assert refresh_module._required_refresh_files(
        {"nt_msg.db", "group_info.db"}
    ) == {"group_info.db", "nt_msg.db"}
