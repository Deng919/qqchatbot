from datetime import date, datetime, timezone
import sqlite3

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.search import search_archive


@pytest.fixture
def archive(tmp_path):
    ar = Archive.open(tmp_path / 'search.sqlite')
    ar.upsert_groups([GroupConfig(group_id=1, name='研发'), GroupConfig(group_id=2, name='产品')])
    yield ar
    ar.close()


def add(ar, text, *, msg_id='1', group=1, sender=1001, kind='text', stamp=None):
    stamp = stamp or datetime(2026, 10, 5, 16, tzinfo=timezone.utc)
    return ar.ingest([NormalizedMessage(msg_id=msg_id, group_id=group, sender_qq=sender,
        message_type=kind, timestamp=stamp, collected_at=stamp, text=text)])


@pytest.mark.parametrize('query', ['配置', 'API', '接口配置', '100%', 'a_b', '"OR"', '😀图', '反\\斜', '空 格'])
def test_index_matches_literal_like_for_chinese_and_special_characters(archive, query):
    add(archive, 'API接口配置 100% a_b "OR" 😀图片 反\\斜 空 格')
    add(archive, '配别的置，接别的口', msg_id='2')
    statements = []
    archive.connection.set_trace_callback(statements.append)
    found = search_archive(archive, query=query, kind='message')
    assert found['total'] == 1
    assert found['results'][0]['id'] == '1'
    assert any('message_search_fts MATCH' in sql for sql in statements)


def test_message_filters_are_combined_before_count_and_pagination(archive):
    for number in range(5):
        add(archive, '文件配置已发', msg_id=str(number), kind='file')
    add(archive, '文件配置已发', msg_id='other-sender', sender=1002, kind='file')
    add(archive, '文件配置已发', msg_id='other-type')
    add(archive, '文件配置已发', msg_id='other-group', group=2, kind='file')
    add(archive, '文件配置已发', msg_id='other-day', kind='file', stamp=datetime(2026, 10, 5, 15, 59, tzinfo=timezone.utc))
    params = dict(query='配置', kind='message', sender_qq=1001, message_type='file', group_id=1,
                  date_from=date(2026, 10, 6), date_to=date(2026, 10, 6), page_size=2)
    pages = [search_archive(archive, **params, page=page) for page in (1, 2, 3)]
    assert [page['total'] for page in pages] == [5] * 3
    assert [item['id'] for page in pages for item in page['results']] == ['4','3','2','1','0']
    assert all(item['message_type'] == 'file' for page in pages for item in page['results'])


def test_index_tracks_update_delete_duplicate_and_transaction_rollback(archive):
    add(archive, '旧配置')
    assert add(archive, '不同配置').skipped == 1
    archive.connection.execute("UPDATE messages SET text='新方案' WHERE msg_id='1'")
    archive.connection.commit()
    assert search_archive(archive, query='旧配置', kind='message')['total'] == 0
    assert search_archive(archive, query='新方案', kind='message')['total'] == 1
    archive.connection.execute("DELETE FROM messages WHERE msg_id='1'")
    archive.connection.rollback()
    assert search_archive(archive, query='新方案', kind='message')['total'] == 1
    archive.connection.execute("DELETE FROM messages WHERE msg_id='1'")
    archive.connection.commit()
    assert search_archive(archive, query='新方案', kind='message')['total'] == 0
    add(archive, '再次配置')
    assert search_archive(archive, query='再次配置', kind='message')['total'] == 1


def test_old_database_backfill_and_native_sql_writer(tmp_path):
    path = tmp_path / 'old.sqlite'
    ar = Archive.open(path)
    ar.upsert_groups([GroupConfig(group_id=1, name='旧群')])
    add(ar, '历史配置')
    # Simulate a pre-index database, keeping original messages.
    for row in ar.connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'message_search_%'").fetchall():
        ar.connection.execute(f'DROP TRIGGER {row[0]}')
    ar.connection.execute('DROP TABLE IF EXISTS message_search_fts')
    ar.connection.execute('DROP TABLE IF EXISTS message_search_meta')
    ar.connection.commit()
    ar.close()
    ar = Archive.open(path)
    assert search_archive(ar, query='历史配置', kind='message')['total'] == 1
    ar.close()
    # Older versions and maintenance connections need no Python custom function.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE messages SET text='直接更新配置'")
    ar = Archive.open(path)
    assert search_archive(ar, query='历史配置', kind='message')['total'] == 0
    assert search_archive(ar, query='直接更新', kind='message')['total'] == 1
    ar.close()


def test_disjoint_bigrams_do_not_match_a_whole_phrase(archive):
    add(archive, '接口，口配，配置')
    assert search_archive(archive, query='接口配置', kind='message')['total'] == 0


def test_fallback_period_forces_rebuild_when_fts_returns(archive):
    from qq_digest.message_search import install_message_index
    add(archive, '最初配置')
    class NoFtsConnection:
        def __enter__(self): return self
        def __exit__(self, *args): archive.connection.commit()
        def execute(self, sql, *args):
            if sql.startswith('CREATE VIRTUAL TABLE temp.message_search_probe'):
                raise sqlite3.OperationalError('no such module: fts5')
            return archive.connection.execute(sql, *args)
    assert install_message_index(NoFtsConnection()) is False
    archive.message_search_indexed=False
    add(archive, '降级期间配置', msg_id='2')
    archive.connection.execute("DELETE FROM messages WHERE msg_id='1'")
    archive.connection.commit()
    assert search_archive(archive, query='配置', kind='message')['total'] == 1
    archive.message_search_indexed=install_message_index(archive.connection)
    assert [row['id'] for row in search_archive(archive, query='配置', kind='message')['results']] == ['2']


def test_failed_initial_backfill_rolls_back_index_schema(tmp_path):
    from qq_digest.message_search import install_message_index
    db=sqlite3.connect(tmp_path/'failed.sqlite')
    db.execute('CREATE TABLE messages(text TEXT,sender_qq INTEGER,timestamp TEXT)')
    class BrokenBuild:
        def __enter__(self): db.__enter__(); return self
        def __exit__(self,*args): return db.__exit__(*args)
        def execute(self,sql,*args):
            if sql.startswith('INSERT INTO message_search_fts(rowid,tokens) SELECT'):
                raise sqlite3.OperationalError('synthetic disk failure')
            return db.execute(sql,*args)
    with pytest.raises(sqlite3.OperationalError,match='disk failure'):
        install_message_index(BrokenBuild())
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'message_search_%'").fetchone()[0]==0
    db.close()


@pytest.mark.parametrize('params', [dict(sender_qq=0), dict(sender_qq=2**63), dict(message_type='image'),
    dict(kind='all', sender_qq=1001), dict(kind='knowledge', message_type='file'), dict(page=0), dict(page_size=101)])
def test_invalid_or_ambiguous_filters_rejected(archive, params):
    with pytest.raises(ValueError):
        search_archive(archive, query='配置', **params)
