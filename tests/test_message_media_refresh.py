import sqlite3

import pytest

import qq_digest.refresh as refresh
from tests.test_message_media_index import TS, current_database
from tests.test_ntqq_collector import ntqq_dbs, ntqq_fts_dbs


def encrypted_fixture(source, target):
    target.write_bytes(b'x' * refresh.EXT_HEADER + source.read_bytes())


@pytest.mark.parametrize('damaged', [True, False])
def test_only_integrity_rejection_exposes_private_recovery_candidate(tmp_path, monkeypatch, damaged):
    plain = current_database(tmp_path / 'source.db', damaged=damaged)
    encrypted = tmp_path / 'encrypted.db'
    encrypted_fixture(plain, encrypted)
    output = tmp_path / 'verified.db'
    output.write_bytes(b'previous verified')
    rejected = tmp_path / 'private-rejected.db'
    monkeypatch.setattr(refresh, '_decrypt_page_image', lambda data, key, page: data)
    result = refresh._decrypt_db(encrypted, output, b'k' * 32,
        required_tables={'group_msg_table'}, check_integrity=True,
        failed_integrity_path=rejected)
    assert result is (not damaged)
    if damaged:
        assert output.read_bytes() == b'previous verified'
        assert rejected.read_bytes() == plain.read_bytes()
    else:
        assert not rejected.exists()
        assert output.read_bytes() == plain.read_bytes()


def configure_refresh(monkeypatch, sources):
    original_isdir = refresh.os.path.isdir
    monkeypatch.setattr(refresh.os.path, 'isdir',
        lambda path: True if 'Tencent Files' in str(path) else original_isdir(path))
    entries = [('global', str(path), bytes([i + 1]) * 16) for i, path in enumerate(sources)]
    monkeypatch.setattr(refresh, '_collect_db_info', lambda _: (entries, {}))
    monkeypatch.setattr(refresh, '_derive_enc_key', lambda *_: b'k' * 32)
    monkeypatch.setattr(refresh, '_verify_key_hmac', lambda *_: True)
    monkeypatch.setattr(refresh, '_decrypt_page_image', lambda data, key, page: data)


@pytest.mark.parametrize('fts_valid', [True, False])
def test_refresh_publishes_optional_media_only_with_current_validated_fts(
        ntqq_fts_dbs, tmp_path, monkeypatch, fts_valid):
    from qq_digest import message_media_index
    encrypted = tmp_path / 'encrypted'
    encrypted.mkdir()
    current = current_database(tmp_path / 'current.db')
    sources = []
    for name, source in [('nt_msg.db', current),
            ('group_info.db', ntqq_fts_dbs / 'group_info.db'),
            ('group_msg_fts.db', ntqq_fts_dbs / 'group_msg_fts.db')]:
        target = encrypted / name
        encrypted_fixture(source, target)
        sources.append(target)
    if not fts_valid:
        sources[-1].write_bytes(b'invalid encrypted file')
    configure_refresh(monkeypatch, sources)
    real_build = message_media_index.build_message_media_snapshot
    monkeypatch.setattr(message_media_index, 'build_message_media_snapshot',
        lambda source, fts, target: real_build(source, fts, target, start_ts=TS, end_ts=TS + 100))
    old_main = (ntqq_fts_dbs / 'nt_msg.db').read_bytes()
    old_fts = (ntqq_fts_dbs / 'group_msg_fts.db').read_bytes()
    result = refresh.refresh_database(qq_number=123, output_dir=ntqq_fts_dbs,
        snapshot_root=tmp_path / 'snapshots', pid=42)
    assert result.success is fts_valid
    assert (ntqq_fts_dbs / 'nt_msg.db').read_bytes() == old_main
    assert (ntqq_fts_dbs / 'group_msg_fts.db').read_bytes() == old_fts
    snapshot = ntqq_fts_dbs / 'nt_msg_media.db'
    assert snapshot.exists() is fts_valid
    if fts_valid:
        with sqlite3.connect(snapshot) as db:
            assert db.execute('SELECT COUNT(*) FROM message_media').fetchone()[0] == 3
            assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]


def test_failed_optional_rebuild_preserves_previous_snapshot(ntqq_fts_dbs, tmp_path, monkeypatch):
    from qq_digest import message_media_index
    encrypted = tmp_path / 'encrypted'
    encrypted.mkdir()
    current = current_database(tmp_path / 'current.db')
    sources = []
    for name, source in [('nt_msg.db', current),
            ('group_info.db', ntqq_fts_dbs / 'group_info.db'),
            ('group_msg_fts.db', ntqq_fts_dbs / 'group_msg_fts.db')]:
        target = encrypted / name
        encrypted_fixture(source, target)
        sources.append(target)
    configure_refresh(monkeypatch, sources)
    snapshot = ntqq_fts_dbs / 'nt_msg_media.db'
    snapshot.write_bytes(b'previous optional snapshot')
    def fail(*args):
        raise sqlite3.DatabaseError('indexed read failed')
    monkeypatch.setattr(message_media_index, 'build_message_media_snapshot', fail)
    result = refresh.refresh_database(qq_number=123, output_dir=ntqq_fts_dbs,
        snapshot_root=tmp_path / 'snapshots', pid=42)
    assert result.success
    assert any('nt_msg_media.db' in error for error in result.errors)
    assert snapshot.read_bytes() == b'previous optional snapshot'
