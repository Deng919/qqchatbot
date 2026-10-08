import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from qq_digest.storage_inventory import inventory


def config(root, **overrides):
    values = dict(data_dir=root, archive_path=root / 'archive.sqlite', report_dir=root / 'reports',
                  knowledge_dir=root / 'knowledge', work_dir=root / 'work', log_dir=root / 'logs',
                  ntqq=SimpleNamespace(db_dir=''), resolve_knowledge_paths=lambda: {})
    values.update(overrides)
    return SimpleNamespace(**values)


def write(path, length):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * length)
    return path


def by_name(result):
    return {row['name']: row for row in result['categories']}


def test_classification_excludes_unknown_sources_and_counts_sqlite_sidecars(tmp_path):
    cfg = config(tmp_path / 'data')
    write(cfg.archive_path, 10)
    write(Path(str(cfg.archive_path) + '-wal'), 11)
    write(Path(str(cfg.archive_path) + '-shm'), 12)
    write(cfg.report_dir / 'report.md', 13)
    write(cfg.knowledge_dir / 'resources.md', 14)
    write(cfg.work_dir / 'snapshots' / 'refresh-123' / 'database.sqlite', 15)
    write(cfg.work_dir / 'snapshots' / 'other' / 'unknown', 1000)
    write(cfg.work_dir / 'raw-qq.sqlite', 1000)
    write(cfg.log_dir / 'run.log', 16)
    backups, cache = tmp_path / 'backups', tmp_path / 'temp'
    write(backups / 'QQDigest-manual-20260101.zip', 17)
    write(backups / 'QQDigest-safety-20260101.zip', 18)
    write(backups / 'other.zip', 1000)
    write(cache / 'qqdigest-backup-123' / 'snapshot.sqlite', 19)
    write(cache / 'qqdigest-check-123' / 'snapshot.sqlite', 20)
    write(cache / 'qqdigest-restore-123' / 'source', 1000)
    result = inventory(cfg, backups, cache)
    rows = by_name(result)
    assert [row['name'] for row in result['categories']] == ['database','reports','knowledge','cache','backups','logs']
    assert [rows[name]['size_bytes'] for name in rows] == [33, 13, 14, 54, 35, 16]
    assert result['total_bytes'] == 165
    assert result['paths'] == {'storage': str(cfg.data_dir), 'backups': str(backups), 'cache': str(cache)}


def test_overlap_and_hardlinks_have_category_precedence(tmp_path):
    root = tmp_path / 'data'
    shared = root / 'shared'
    cfg = config(root, report_dir=shared, knowledge_dir=shared, log_dir=shared)
    write(cfg.archive_path, 10)
    write(shared / 'first.md', 20)
    os.link(shared / 'first.md', shared / 'second.md')
    os.link(cfg.archive_path, shared / 'archive-copy')
    rows = by_name(inventory(cfg, tmp_path / 'backups', tmp_path / 'temp'))
    assert rows['database']['size_bytes'] == 10
    assert rows['reports']['size_bytes'] == 20
    assert rows['reports']['file_count'] == 1
    assert rows['knowledge']['file_count'] == rows['logs']['file_count'] == 0


def test_shared_storage_root_uses_database_references_only(tmp_path):
    root = tmp_path / 'data'
    cfg = config(root, report_dir=root, knowledge_dir=root, log_dir=root)
    write(root / 'unrelated-personal-file', 999)
    report = write(root / 'saved.md', 21)
    knowledge = write(root / 'saved-knowledge.md', 22)
    with sqlite3.connect(cfg.archive_path) as db:
        db.execute('CREATE TABLE reports(markdown_path TEXT, json_path TEXT)')
        db.execute('INSERT INTO reports VALUES (?,?)', (str(report), ''))
        db.execute('CREATE TABLE knowledge_items(markdown_path TEXT)')
        db.execute('INSERT INTO knowledge_items VALUES (?)', (str(knowledge),))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = inventory(cfg, tmp_path / 'missing-backups', tmp_path / 'missing-temp')
    rows = by_name(result)
    assert rows['reports']['size_bytes'] == 21
    assert rows['knowledge']['size_bytes'] == 22
    assert rows['logs']['size_bytes'] == 0
    assert result['warnings']
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert not (tmp_path / 'missing-backups').exists()
    assert not (tmp_path / 'missing-temp').exists()


def test_known_external_knowledge_file_and_qq_source_exclusion(tmp_path):
    data, raw = tmp_path / 'data', tmp_path / 'raw-qq'
    external = write(tmp_path / 'custom-knowledge' / 'resources.md', 31)
    write(raw / 'messages.sqlite', 1000)
    cfg = config(data, report_dir=raw, knowledge_dir=raw, ntqq=SimpleNamespace(db_dir=str(raw)),
                 resolve_knowledge_paths=lambda: {'resource': external, 'experience': raw / 'messages.sqlite'})
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    rows = by_name(result)
    assert rows['knowledge']['size_bytes'] == 31
    assert rows['reports']['size_bytes'] == 0
    assert result['total_bytes'] == 31
    assert result['warnings']


def test_symlink_ancestor_and_child_are_skipped(tmp_path):
    real = tmp_path / 'real'
    write(real / 'reports' / 'private.md', 1000)
    linked = tmp_path / 'linked'
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation needs OS permission')
    cfg = config(tmp_path / 'data', report_dir=linked / 'reports')
    write(cfg.knowledge_dir / 'safe.md', 8)
    (cfg.knowledge_dir / 'outside').symlink_to(real, target_is_directory=True)
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    assert result['total_bytes'] == 8
    assert len(result['warnings']) >= 2


def test_missing_inventory_is_read_only_and_disk_is_deduplicated(tmp_path):
    root = tmp_path / 'missing'
    result = inventory(config(root), root / 'backups', root / 'temp')
    assert result['total_bytes'] == 0
    assert not root.exists()
    assert len(result['disks']) == 1
    assert type(result['disks'][0]['free_bytes']) is int


def test_junction_ancestor_is_rejected_on_windows(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows junction test')
    import subprocess
    real = tmp_path / 'real'
    write(real / 'reports' / 'private.md', 777)
    linked = tmp_path / 'linked'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(real)], capture_output=True)
    if result.returncode:
        pytest.skip('junction creation unavailable')
    cfg = config(tmp_path / 'data', report_dir=linked / 'reports')
    try:
        scanned = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
        assert scanned['total_bytes'] == 0
        assert by_name(scanned)['reports']['complete'] is False
        assert by_name(scanned)['knowledge']['complete'] is True
        assert scanned['total_complete'] is False
        assert any('重解析点' in warning for warning in scanned['warnings'])
    finally:
        # rmdir removes only the junction, never recurse into the target.
        os.rmdir(linked)


def test_directory_permission_failure_has_warning(tmp_path, monkeypatch):
    cfg = config(tmp_path / 'data')
    write(cfg.report_dir / 'report.md', 10)
    original = os.scandir
    def denied(path):
        if Path(path) == cfg.report_dir:
            raise PermissionError('synthetic denied')
        return original(path)
    monkeypatch.setattr(os, 'scandir', denied)
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    assert by_name(result)['reports']['size_bytes'] == 0
    assert by_name(result)['reports']['complete'] is False
    assert by_name(result)['knowledge']['complete'] is True
    assert result['total_complete'] is False
    assert any('synthetic denied' in warning for warning in result['warnings'])


def test_shared_user_documents_root_only_counts_known_files(tmp_path, monkeypatch):
    user = tmp_path / 'user'
    documents = user / 'Documents'
    write(documents / 'private.doc', 999)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: user))
    cfg = config(tmp_path / 'data', report_dir=documents)
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    assert by_name(result)['reports']['size_bytes'] == 0
    assert any('共享' in warning for warning in result['warnings'])


def test_scanning_existing_wal_never_mutates_files_and_warns_on_partial_references(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    cfg = config(root, report_dir=root)
    report = write(root / 'report.md', 12)
    with sqlite3.connect(cfg.archive_path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE reports(markdown_path TEXT, json_path TEXT)')
        db.execute('INSERT INTO reports VALUES (?,?)', (str(report), ''))
        db.commit()
        before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
        result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
        after = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
        assert before == after
        assert any('WAL' in warning for warning in result['warnings'])
        assert by_name(result)['reports']['complete'] is False
        assert by_name(result)['database']['complete'] is True


def test_link_dotdot_cannot_hide_junction(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows junction test')
    import subprocess
    real = tmp_path / 'real'
    real.mkdir()
    linked = tmp_path / 'linked'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(real)], capture_output=True)
    if result.returncode:
        pytest.skip('junction creation unavailable')
    root = tmp_path / 'data'
    write(root / 'reports' / 'safe.md', 40)
    cfg = config(root, report_dir=linked / '..' / 'data' / 'reports')
    try:
        result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
        assert by_name(result)['reports']['size_bytes'] == 0
        assert any('重解析点' in warning for warning in result['warnings'])
    finally:
        os.rmdir(linked)


def test_disappearing_file_has_incomplete_warning(tmp_path, monkeypatch):
    cfg = config(tmp_path / 'data')
    report = write(cfg.report_dir / 'report.md', 10)
    original = Path.lstat
    def disappearing(path, *args, **kwargs):
        if path == report:
            raise FileNotFoundError('synthetic disappeared')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', disappearing)
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    assert by_name(result)['reports']['file_count'] == 0
    assert by_name(result)['reports']['complete'] is False
    assert any('变化' in warning for warning in result['warnings'])


def test_empty_existing_and_missing_categories_are_confirmed_complete(tmp_path):
    cfg = config(tmp_path / 'data')
    cfg.report_dir.mkdir(parents=True)
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    assert result['total_bytes'] == 0
    assert result['total_complete'] is True
    assert all(row['complete'] is True for row in result['categories'])


def test_shared_root_is_lower_bound_but_intentional_qq_exclusion_is_complete(tmp_path):
    root, raw = tmp_path / 'data', tmp_path / 'raw'
    write(root / 'unidentified', 10)
    cfg = config(root, report_dir=root, knowledge_dir=raw, ntqq=SimpleNamespace(db_dir=str(raw)))
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    rows = by_name(result)
    assert rows['reports']['complete'] is False
    assert rows['knowledge']['complete'] is True
    assert result['total_complete'] is False


def test_readonly_reference_connection_is_explicitly_closed(tmp_path, monkeypatch):
    cfg = config(tmp_path / 'data')
    cfg.data_dir.mkdir()
    with sqlite3.connect(cfg.archive_path) as db:
        db.execute('CREATE TABLE reports(markdown_path TEXT, json_path TEXT)')
    original = sqlite3.connect
    opened = []
    class TrackedConnection(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            super().close()
    def track(*args, **kwargs):
        connection = original(*args, factory=TrackedConnection, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', track)
    inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    assert opened and all(connection.closed for connection in opened)


def test_path_permission_failure_affects_only_its_category(tmp_path, monkeypatch):
    cfg = config(tmp_path / 'data')
    write(cfg.report_dir / 'report.md', 10)
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path == cfg.report_dir:
            raise PermissionError('synthetic path denied')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', denied)
    result = inventory(cfg, tmp_path / 'backups', tmp_path / 'temp')
    rows = by_name(result)
    assert rows['reports']['size_bytes'] == 0
    assert rows['reports']['complete'] is False
    assert all(row['complete'] for name, row in rows.items() if name != 'reports')
    assert result['total_complete'] is False
