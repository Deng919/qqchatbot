import sqlite3
from datetime import date
from pathlib import Path
import pytest
import yaml

from qq_digest.archive import Archive
from qq_digest.config import load_config
from qq_digest.models import GroupConfig


@pytest.fixture
def setup_env(tmp_path):
    path = tmp_path / 'config' / 'config.yaml'
    path.parent.mkdir()
    path.write_text(yaml.safe_dump({'security': {'web_password_hash': 'fake'},
        'ai': {'base_url': 'https://example.com/v1', 'model': 'sample', 'api_key_env': 'TEST_KEY'},
        'custom': {'preserve': 42}, 'collection': {'enabled': False}}), encoding='utf-8')
    cfg = load_config(path)
    archive = Archive.open(cfg.archive_path)
    source = tmp_path / 'source'
    source.mkdir()
    with sqlite3.connect(source / 'group_info.db') as db:
        db.execute('CREATE TABLE group_list ("60001" INTEGER, "60007" TEXT, "60040" BLOB)')
        db.executemany('INSERT INTO group_list VALUES (?, ?, ?)', [(100, '研发', b''), (200, '产品', b'')])
    with sqlite3.connect(source / 'group_msg_fts.db') as db:
        db.execute('CREATE TABLE group_msg_fts ("41700" INTEGER PRIMARY KEY, "40001" INTEGER, "40050" INTEGER, "40020" TEXT, "40021" TEXT, "40027" INTEGER, "41701" TEXT, "41702" TEXT, "41703" TEXT, "41704" TEXT, "41705" TEXT, "41706" TEXT, "41707" TEXT)')
        from datetime import datetime
        from zoneinfo import ZoneInfo
        timestamp = int(datetime(2026, 10, 5, 12, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        db.execute('INSERT INTO group_msg_fts VALUES (1, 901, ?, "sender", "100", 123, "真实测试消息", "", "2", "1", "", "", "")', (timestamp,))
    yield path, cfg, archive, source
    archive.close()


def service(env):
    from qq_digest.first_use import FirstUseService
    path, cfg, archive, _ = env
    return FirstUseService(cfg, path, archive)


def test_source_preserves_configuration_and_updates_same_runtime_object(setup_env):
    svc = service(setup_env)
    path, cfg, _, source = setup_env
    identity = id(cfg)
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    assert id(cfg) == identity and cfg.ntqq.enabled and cfg.ntqq.db_dir == str(source.resolve())
    assert yaml.safe_load(path.read_text('utf-8'))['custom'] == {'preserve': 42}
    assert state['active'] and state['step'] == 2
    assert state['revision'] == service(setup_env).snapshot()['revision']


def test_config_conflict_and_atomic_failure_keep_existing_config(setup_env, monkeypatch):
    from qq_digest.first_use import SetupConflict
    svc = service(setup_env)
    path, cfg, _, source = setup_env
    revision = svc.snapshot()['revision']
    original = path.read_bytes()
    import qq_digest.first_use as module
    monkeypatch.setattr(module.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): svc.set_source(revision, str(source), 0)
    assert path.read_bytes() == original and not cfg.ntqq.enabled
    path.write_bytes(original + b'\n# external edit\n')
    with pytest.raises(SetupConflict): svc.set_source(revision, str(source), 0)
    assert not cfg.ntqq.enabled


def test_selection_import_resume_dedup_and_zero_messages(setup_env):
    svc = service(setup_env)
    _, _, archive, source = setup_env
    archive.upsert_groups([GroupConfig(group_id=100, name='自定义', enabled=False, keywords=['保留'], daily_summary=False), GroupConfig(group_id=300, name='其他')])
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    state = svc.select_groups(state['revision'], [100, 200])
    existing = {g.group_id: g for g in archive.all_groups()}
    assert existing[100].keywords == ['保留'] and existing[100].name == '自定义'
    assert existing[100].enabled and not existing[100].daily_summary and existing[300].enabled
    start = end = date(2026, 10, 5)
    state = svc.import_group(state['revision'], 100, start, end)
    assert state['imports']['100']['inserted'] == 1
    svc = service(setup_env)
    state = svc.import_group(state['revision'], 100, start, end)
    assert state['imports']['100']['inserted'] == 0
    state = svc.import_group(state['revision'], 200, start, end)
    assert state['imports']['200']['total'] == 0
    assert archive.connection.execute('SELECT last_timestamp FROM sync_state WHERE group_id=100').fetchone()[0] is None
    preview = svc.preview()
    assert preview['total_messages'] == 1 and preview['groups'][1]['message_count'] == 0
    assert preview['samples'][0]['text'] == '真实测试消息'
    state = svc.skip_ai(state['revision'])
    state = svc.set_schedule(state['revision'], False, False, 20, 21, 15, state['features_revision'])
    state = svc.finish(state['revision'])
    assert state['completed'] and not state['active']


def test_changed_source_invalidates_import_and_failure_is_retryable(setup_env):
    svc = service(setup_env)
    _, _, _, source = setup_env
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    state = svc.select_groups(state['revision'], [100])
    day = date(2026, 10, 5)
    with sqlite3.connect(source / 'group_msg_fts.db') as db: db.execute('DROP TABLE group_msg_fts')
    state = svc.import_group(state['revision'], 100, day, day)
    assert state['imports']['100']['status'] == 'failed'
    with pytest.raises(ValueError): svc.finish(state['revision'])


def test_schedule_requires_source_account_and_ai_ready(setup_env):
    svc = service(setup_env)
    _, _, _, source = setup_env
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    state = svc.skip_ai(state['revision'])
    with pytest.raises(ValueError): svc.set_schedule(state['revision'], True, False, 10, 22, 0, state['features_revision'])
    with pytest.raises(ValueError): svc.set_schedule(state['revision'], False, True, 10, 22, 0, state['features_revision'])


def test_detection_is_readonly_and_does_not_read_account_secrets(tmp_path):
    from qq_digest.first_use import detect_accounts
    root = tmp_path / 'Tencent Files'
    (root / '123456' / 'nt_qq' / 'nt_db').mkdir(parents=True)
    (root / 'not-an-account').mkdir()
    found = detect_accounts(root)
    assert [item['qq_number'] for item in found] == [123456]
    assert len(list(root.rglob('*'))) == 4


def test_group_selection_rolls_back_when_config_write_fails(setup_env, monkeypatch):
    svc = service(setup_env)
    _, _, archive, source = setup_env
    archive.upsert_groups([GroupConfig(group_id=100, name='自定义', enabled=False)])
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    monkeypatch.setattr('qq_digest.first_use.os.replace', lambda *_: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): svc.select_groups(state['revision'], [100, 200])
    assert {g.group_id: g.enabled for g in archive.all_groups()} == {100: False}


def test_range_change_clears_other_groups_and_repeat_failure_can_recover(setup_env, monkeypatch):
    from qq_digest.collector.ntqq import NTQQCollector
    svc = service(setup_env)
    _, _, _, source = setup_env
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    state = svc.select_groups(state['revision'], [100, 200])
    day = date(2026, 10, 5)
    original = NTQQCollector.collect
    monkeypatch.setattr(NTQQCollector, 'collect', lambda *_: (_ for _ in ()).throw(OSError('bad source')))
    state = svc.import_group(state['revision'], 100, day, day)
    assert state['imports']['100']['status'] == 'failed'
    monkeypatch.setattr(NTQQCollector, 'collect', original)
    for gid in [100, 200]: state = svc.import_group(state['revision'], gid, day, day)
    assert all(r['status'] == 'success' for r in state['imports'].values())
    state = svc.import_group(state['revision'], 100, date(2026, 10, 4), day)
    assert set(state['imports']) == {'100'}


def test_source_reselection_discards_old_import_results(setup_env):
    svc = service(setup_env)
    _, _, _, source = setup_env
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    state = svc.select_groups(state['revision'], [100])
    state = svc.import_group(state['revision'], 100, date(2026, 10, 5), date(2026, 10, 5))
    state = svc.set_source(state['revision'], str(source), 123456)
    assert state['selected_groups'] == [] and state['imports'] == {}


def test_schedule_file_failure_keeps_feature_flags_and_configuration(setup_env, monkeypatch):
    svc = service(setup_env)
    path, cfg, archive, source = setup_env
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    state = svc.skip_ai(state['revision'])
    from qq_digest.features import FeatureService
    before = FeatureService(archive).snapshot()
    content = path.read_bytes()
    monkeypatch.setattr('qq_digest.first_use.os.replace', lambda *_: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): svc.set_schedule(state['revision'], False, False, 20, 21, 15, state['features_revision'])
    assert FeatureService(archive).snapshot() == before
    assert path.read_bytes() == content and cfg.summary.hour == 22


def test_database_commit_failure_restores_configuration_atomically(setup_env, monkeypatch):
    svc = service(setup_env)
    path, cfg, archive, source = setup_env
    state = svc.set_source(svc.snapshot()['revision'], str(source), 0)
    content = path.read_bytes()
    worker = Archive.open(cfg.archive_path)
    connection = worker.connection
    class FailedCommit:
        def __getattr__(self, key): return getattr(connection, key)
        def commit(self): raise sqlite3.OperationalError('commit failed')
    worker.connection = FailedCommit()
    monkeypatch.setattr(Archive, 'open', classmethod(lambda cls, _: worker))
    with pytest.raises(sqlite3.OperationalError): svc.select_groups(state['revision'], [100, 200])
    assert path.read_bytes() == content
    assert svc.snapshot()['revision'] == state['revision']
    assert svc.snapshot()['selected_groups'] == [] and archive.all_groups() == []
