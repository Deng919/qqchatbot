"""Rebuildable Chinese substring index, synchronized by native SQLite triggers."""
from __future__ import annotations

import sqlite3

MESSAGE_TYPES = {'text', 'link', 'file', 'reply', 'forward', 'system', 'unknown'}
_ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


def search_tokens(text: str) -> str:
    """Only ASCII folds, matching SQLite's default literal LIKE semantics."""
    text = text.translate(_ASCII_LOWER)
    return ' AND '.join(sorted({'x' + text[i:i + 2].encode('utf-8').hex()
                               for i in range(len(text) - 1)}))


def _sql_tokens(value: str) -> str:
    # SQL built-ins keep older application versions and raw SQLite writers able
    # to maintain the index. Encoded pairs also index punctuation and emoji.
    return f"""(WITH RECURSIVE positions(i) AS (
        SELECT 1 WHERE length({value})>=2
        UNION ALL SELECT i+1 FROM positions WHERE i<length({value})-1
    ) SELECT COALESCE(group_concat('x'||hex(substr(lower({value}),i,2)), ' '),'')
      FROM positions)"""


def install_message_index(connection: sqlite3.Connection) -> bool:
    """Atomically build once; never change message identity or ingest cursors."""
    with connection:
        connection.execute('SAVEPOINT message_search_install')
        present = connection.execute("SELECT 1 FROM sqlite_master WHERE name='message_search_fts'").fetchone()
        try:
            # IF NOT EXISTS alone does not load a virtual table's module.
            # Probe a new temporary table even when the archive already has one.
            connection.execute('CREATE VIRTUAL TABLE temp.message_search_probe USING fts5(tokens)')
            connection.execute('DROP TABLE temp.message_search_probe')
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS message_search_fts USING fts5(tokens, content='', tokenize='ascii')")
        except sqlite3.OperationalError as exc:
            if 'no such module: fts5' in str(exc).lower():
                # If moved to a runtime without FTS5, retain the index for a
                # capable runtime but allow plain collection/search to continue.
                for suffix in ('insert', 'update', 'delete'):
                    connection.execute(f'DROP TRIGGER IF EXISTS message_search_{suffix}')
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name='message_search_meta'").fetchone():
                    connection.execute('DELETE FROM message_search_meta')
                return False
            raise
        connection.execute('CREATE TABLE IF NOT EXISTS message_search_meta (version INTEGER PRIMARY KEY)')
        ready = connection.execute('SELECT 1 FROM message_search_meta WHERE version=1').fetchone()
        for suffix in ('insert', 'update', 'delete'):
            connection.execute(f'DROP TRIGGER IF EXISTS message_search_{suffix}')
        new, old = _sql_tokens('new.text'), _sql_tokens('old.text')
        connection.execute(f"""CREATE TRIGGER message_search_insert AFTER INSERT ON messages BEGIN
            INSERT INTO message_search_fts(rowid,tokens) VALUES(new.rowid,{new}); END""")
        connection.execute(f"""CREATE TRIGGER message_search_delete AFTER DELETE ON messages BEGIN
            INSERT INTO message_search_fts(message_search_fts,rowid,tokens) VALUES('delete',old.rowid,{old}); END""")
        connection.execute(f"""CREATE TRIGGER message_search_update AFTER UPDATE OF text,rowid ON messages BEGIN
            INSERT INTO message_search_fts(message_search_fts,rowid,tokens) VALUES('delete',old.rowid,{old});
            INSERT INTO message_search_fts(rowid,tokens) VALUES(new.rowid,{new}); END""")
        if not present or not ready:
            connection.execute("INSERT INTO message_search_fts(message_search_fts) VALUES('delete-all')")
            connection.execute(f'INSERT INTO message_search_fts(rowid,tokens) SELECT m.rowid,{_sql_tokens("m.text")} FROM messages m')
            connection.execute('INSERT OR IGNORE INTO message_search_meta(version) VALUES(1)')
        connection.execute('CREATE INDEX IF NOT EXISTS idx_messages_sender_time ON messages(sender_qq,timestamp)')
    return True
