import sqlite3

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig
from qq_digest.search import search_archive
from tests.factories import make_message


def test_initialize_empty_archive(tmp_path):
    from qq_digest.archive_schema import initialize_archive_schema

    with sqlite3.connect(tmp_path / "archive.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        initialize_archive_schema(connection)
        for table, required in {
            "groups": {"category", "read_images", "read_files"},
            "tasks": {"revision"},
            "reports": {"input_fingerprint", "source_message_count"},
            "jobs": {"target_date", "retry_of_job_id"},
            "send_log": {"manual_retry_count", "recipient"},
        }.items():
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            assert required <= columns
        assert connection.execute(
            "SELECT revision FROM feature_settings"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT high_water FROM message_ingest_sequence"
        ).fetchone()[0] == 0


def test_initialize_legacy_groups_preserves_saved_configuration(tmp_path):
    from qq_digest.archive_schema import initialize_archive_schema

    with sqlite3.connect(tmp_path / "legacy.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE groups (
                group_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                enabled INTEGER NOT NULL, daily_summary INTEGER NOT NULL,
                template TEXT NOT NULL, keywords TEXT NOT NULL,
                important_candidates INTEGER NOT NULL,
                collection_window_days INTEGER NOT NULL)"""
        )
        connection.execute(
            "INSERT INTO groups VALUES(?,?,?,?,?,?,?,?)",
            (123, "旧群", 0, 1, "concise", '["资料"]', 1, 7),
        )
        initialize_archive_schema(connection)
        saved = dict(connection.execute("SELECT * FROM groups").fetchone())
        schema = [
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            )
        ]

        initialize_archive_schema(connection)

        assert dict(connection.execute("SELECT * FROM groups").fetchone()) == saved
        assert (
            saved["name"], saved["enabled"], saved["keywords"],
            saved["collection_window_days"],
        ) == ("旧群", 0, '["资料"]', 7)
        assert (
            saved["category"], saved["read_images"], saved["read_files"],
        ) == ("general", 0, 0)
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            )
        ] == schema


def test_reopening_archive_preserves_saved_data_and_search(tmp_path):
    path = tmp_path / "archive.sqlite"
    message = make_message()
    with_message_group = GroupConfig(
        group_id=message.group_id, name="已保存的群", collection_window_days=7,
        read_images=True,
    )
    archive = Archive.open(path)
    try:
        archive.seed_groups([with_message_group])
        archive.ingest([message])
        archive.mark_sync(group_id=message.group_id, last_timestamp=message.timestamp)
        saved_message = tuple(archive.connection.execute(
            "SELECT rowid,* FROM messages"
        ).fetchone())
        saved_sync = tuple(archive.connection.execute(
            "SELECT * FROM sync_state"
        ).fetchone())
        schema = [tuple(row) for row in archive.connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )]
    finally:
        archive.close()

    for _ in range(2):
        archive = Archive.open(path)
        try:
            assert archive.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert tuple(archive.connection.execute(
                "SELECT rowid,* FROM messages"
            ).fetchone()) == saved_message
            assert tuple(archive.connection.execute(
                "SELECT * FROM sync_state"
            ).fetchone()) == saved_sync
            assert archive.all_groups() == [with_message_group]
            assert [tuple(row) for row in archive.connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            )] == schema
            assert search_archive(
                archive, query=message.text, kind="message"
            )["total"] == 1
        finally:
            archive.close()
