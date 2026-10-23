"""Storage transfer regressions use only synthetic archives and local fake sinks."""
import asyncio
import json
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.config import load_config
from qq_digest.desktop_settings import DesktopBridge
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.operations import OperationBusy, OperationCoordinator
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher


@pytest.fixture
def runtime(tmp_path):
    source = tmp_path / 'source'
    (source / 'config').mkdir(parents=True)
    config_path = source / 'config' / 'config.yaml'
    (source/'config'/'session-secret.txt').write_text('synthetic-session-secret-'*3,encoding='utf-8')
    config_path.write_text('security:\n  web_password_hash: test\n  session_secret_file: config/session-secret.txt\nai:\n  model: test\n'
                           '  base_url: https://example.com\n  api_key_env: TEST_KEY\n', encoding='utf-8')
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'launcher.json').write_text(json.dumps({'config_path': str(config_path)}), encoding='utf-8')
    bridge = DesktopBridge(install, config_path, install / 'app.exe', gui=None,
        backup_root=tmp_path / 'backups', temp_root=tmp_path / 'cache')
    app = create_app(archive=archive, candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(config.knowledge_dir), config=config, config_path=config_path,
        password_hash=PasswordHasher.hash('password123'), session_secret='test-secret')
    app.state.desktop_bridge = bridge
    bridge._operations = app.state.operations
    client = TestClient(app)
    client.post('/login', data={'password': 'password123'})
    app.state.features.update({'topics': True, 'reminders': True}, 0)
    yield client, bridge, tmp_path
    app.state.close_topic_tracking()
    archive.close()


@pytest.mark.parametrize('transfer', ['migrate', 'restore'])
def test_handoff_blocks_all_writes_keeps_reads_and_new_runtime_writable(runtime, transfer):
    client, bridge, root = runtime
    if transfer == 'migrate':
        response = client.post('/api/desktop-settings/migrate', json={'destination': str(root / 'new-data')})
    else:
        backup = bridge.backup_data()
        preview = bridge.preview_restore(backup['path'])
        response = client.post('/api/desktop-settings/restore', json={'path': backup['path'],
            'destination': str(root / 'new-data'), 'sha256': preview['sha256']})
    assert response.status_code == 200, response.text
    assert response.json()['restart_required'] is True
    for method, url, body in [('PATCH', '/api/groups/123', {}), ('POST', '/api/tasks/from-message', {}),
        ('PUT', '/api/ai-settings/key', {'api_key': 'synthetic'}),
        ('POST', '/api/desktop-settings/backup-schedule', {'schedule': 'off'}),
        ('DELETE', '/api/reminder-rules/1', None)]:
        denied = client.request(method, url, json=body)
        assert denied.status_code == 409, (url, denied.text)
        assert '重启' in denied.json()['detail']
    state = client.get('/api/runtime-state').json()
    assert state['read_only'] and state['restart_required']
    assert 'storage-handoff-banner' in client.get('/settings').text
    assert client.get('/api/groups').status_code == 200
    assert client.get('/api/topics').status_code == 200
    assert client.get('/api/tasks').status_code == 200
    assert client.get('/api/reminders').status_code == 200
    assert client.get('/api/reminder-rules').status_code == 200
    operations = client.app.state.operations
    for kind in ('daily', 'sync', 'history_inspection', 'reminder_mutation', 'backup', 'storage_mutation', 'knowledge_mutation'):
        assert not operations.can_start(kind)
        with pytest.raises(OperationBusy, match='重启'):
            with operations.claim(kind):
                pytest.fail('pending runtime allowed a new worker')
    assert bridge.maybe_scheduled_backup() is None
    assert bridge.maybe_scheduled_cleanup() is None
    assert client.post('/logout', follow_redirects=False).status_code == 303
    assert client.post('/login', data={'password': 'password123'}).status_code == 200
    new_config = Path(json.loads((bridge._install_dir / 'launcher.json').read_text())['config_path'])
    restarted = DesktopBridge(bridge._install_dir, new_config, bridge._executable, gui=None,
        backup_root=bridge._backup_root, temp_root=bridge._temp_root)
    client.app.state.desktop_bridge = restarted
    restarted._operations = operations
    assert not client.get('/api/runtime-state').json()['read_only']
    assert operations.can_start('daily')
    assert client.post('/api/desktop-settings/backup-schedule', json={'schedule': 'off'}).status_code == 200


def test_migration_rejects_preexisting_uncoordinated_http_writer(runtime):
    client, bridge, root = runtime
    entered, release = Event(), Event()
    @client.app.post('/test-write')
    async def write():
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return {'ok': True}
    result = []
    thread = Thread(target=lambda: result.append(client.post('/test-write')))
    thread.start()
    assert entered.wait(3)
    try:
        response = client.post('/api/desktop-settings/migrate', json={'destination': str(root / 'new-data')})
        assert response.status_code == 409
        assert not (root / 'new-data').exists()
    finally:
        release.set()
        thread.join(5)
    assert result[0].status_code == 200


def test_pending_direct_bridge_mutations_fail_and_status_is_safe(runtime):
    client, bridge, root = runtime
    bridge.migrate_storage(str(root / 'new-data'))
    with pytest.raises(OperationBusy, match='重启'):
        bridge.set_backup_schedule('off')
    # A malformed launcher must fail closed and expose only a user-facing state.
    (bridge._install_dir / 'launcher.json').write_text('{bad', encoding='utf-8')
    state = client.get('/api/runtime-state').json()
    assert state['read_only'] and 'config_path' not in json.dumps(state)


def test_pending_usage_and_storage_reads_do_not_change_database(runtime):
    client, bridge, root = runtime
    bridge.migrate_storage(str(root / 'new-data'))
    original = bridge._config_path.parent.parent / 'archive' / 'archive.sqlite'
    before = original.read_bytes()
    changes = client.app.state.archive.connection.total_changes
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/groups').status_code == 200
    assert client.get('/api/ai-usage').status_code == 200
    assert client.get('/api/storage').status_code == 200
    assert client.get('/api/reminders').status_code == 200
    assert client.get('/api/reminder-rules').status_code == 200
    assert original.read_bytes() == before
    assert client.app.state.archive.connection.total_changes == changes


def test_pending_bot_event_does_not_mutate_or_send(runtime):
    from types import SimpleNamespace
    from qq_digest.notify.websocket import WebSocketListener
    client, bridge, root = runtime
    calls = []
    listener = WebSocketListener(notifier=SimpleNamespace(send_private_message=lambda *args: calls.append('send')),
        handler=SimpleNamespace(handle_message=lambda *args: calls.append('command') or SimpleNamespace(message='reply')))
    listener.operations = client.app.state.operations
    bridge.migrate_storage(str(root / 'new-data'))
    asyncio.run(listener._handle_event(None, {'op': 0, 't': 'C2C_MESSAGE_CREATE',
        'd': {'author': {'user_openid': 'synthetic'}, 'content': '/ignore 1'}}))
    assert calls == []


def test_notification_worker_holds_claim_on_cancel(runtime, monkeypatch):
    from qq_digest.web import storage_handoff
    from qq_digest.notify import NotificationQueueProcessor
    client, bridge, root = runtime
    entered, release = Event(), Event()
    def process(self):
        entered.set()
        release.wait(5)
    monkeypatch.setattr(NotificationQueueProcessor, 'process_due', process)
    assert hasattr(storage_handoff, 'run_notification_queue'), 'notification queue needs a guarded operation claim'
    async def check():
        task = asyncio.create_task(storage_handoff.run_notification_queue(client.app.state.config,
            client.app.state.operations, object()))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        try:
            assert not client.app.state.operations.can_start('storage_mutation')
            with pytest.raises(OperationBusy):
                bridge.migrate_storage(str(root / 'new-data'))
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert client.app.state.operations.can_start('storage_mutation')
    asyncio.run(check())


def test_pending_automatic_checks_do_not_open_archive(runtime, monkeypatch):
    from qq_digest.reminder_scheduler import run_reminder_check
    from qq_digest.web.history_inspection import run_scheduled_inspection
    from qq_digest.web.storage_handoff import run_notification_queue
    client, bridge, root = runtime
    bridge.migrate_storage(str(root / 'new-data'))
    def forbidden(*args, **kwargs):
        pytest.fail('pending automatic task opened a writable archive')
    monkeypatch.setattr(Archive, 'open', forbidden)
    cfg = client.app.state.config
    cfg.ntqq.enabled = True
    cfg.ntqq.db_dir = root / 'synthetic-ntqq'
    operations = client.app.state.operations
    async def check():
        assert await run_reminder_check(cfg.archive_path, operations, enabled=lambda: True) is False
        assert await run_scheduled_inspection(cfg, operations) is False
        assert await run_notification_queue(cfg, operations, object()) is None
    asyncio.run(check())


@pytest.mark.parametrize('method,args', [('export_reports', ('', 'both')),
    ('set_backup_schedule', ('off',)), ('set_auto_start', (True,))])
def test_direct_bridge_writes_reject_inflight_transfer(runtime, method, args):
    client, bridge, root = runtime
    if method == 'export_reports':
        args = (str(root / 'exports'), 'both')
    with client.app.state.operations.claim('storage_mutation'):
        with pytest.raises(OperationBusy):
            getattr(bridge, method)(*args)


def test_canceled_http_writer_retains_admission_until_worker_finishes(runtime, monkeypatch):
    import httpx
    import importlib
    app_module = importlib.import_module('qq_digest.web.app')
    client, bridge, root = runtime
    entered, release = Event(), Event()
    key_path = root / 'synthetic-key.txt'
    client.app.state.config.ai.ui_api_key_file = str(key_path)
    def save(path, value):
        entered.set()
        release.wait(5)
        Path(path).write_text(value, encoding='utf-8')
    monkeypatch.setattr(app_module, 'save_ui_api_key', save)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
            base_url='http://testserver', cookies=client.cookies.jar) as http:
            task = asyncio.create_task(http.put('/api/ai-settings/key', json={'api_key': 'synthetic'}))
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(.02)
            try:
                assert not client.app.state.operations.can_start('storage_mutation')
                with pytest.raises(OperationBusy):
                    bridge.migrate_storage(str(root / 'new-data'))
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert client.app.state.operations.can_start('storage_mutation')
    asyncio.run(check())
    assert key_path.read_text(encoding='utf-8') == 'synthetic'


def test_pending_archive_qa_source_is_readable(runtime):
    from datetime import datetime, timezone
    from qq_digest.models import GroupConfig, NormalizedMessage
    client, bridge, root = runtime
    archive = client.app.state.archive
    archive.upsert_groups([GroupConfig(group_id=11, name='synthetic')])
    archive.ingest([NormalizedMessage(group_id=11, msg_id='synthetic', sender_qq=1,
        timestamp=datetime(2026, 10, 5, 12, tzinfo=timezone.utc), collected_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
        message_type='text', text='接口配置方案A', content_json={})])
    state = client.app.state.features.snapshot()
    client.app.state.features.update({'archive_qa': True}, state['revision'])
    preview = client.post('/api/archive-qa/preview', json={'question': '接口配置如何修改？',
        'query': '配置', 'group_id': 11, 'date_from': '2026-10-05', 'date_to': '2026-10-06'})
    assert preview.status_code == 200, preview.text
    ticket = preview.json()['ticket']
    bridge.migrate_storage(str(root / 'new-data'))
    response = client.get(f'/api/archive-qa/sources/{ticket}/s1')
    assert response.status_code == 200, response.text
    assert response.json()['msg_id'] == 'synthetic'


def test_missing_launcher_does_not_reenable_old_runtime_writes(runtime):
    client, bridge, root = runtime
    bridge.migrate_storage(str(root / 'new-data'))
    (bridge._install_dir / 'launcher.json').unlink()
    state = client.get('/api/runtime-state').json()
    assert state['read_only'] is True
    assert not client.app.state.operations.can_start('sync')
    assert client.post('/api/desktop-settings/backup-schedule', json={'schedule': 'off'}).status_code == 409


def test_launcherless_development_bridge_remains_writable(tmp_path):
    bridge = DesktopBridge(tmp_path / 'development', tmp_path / 'config.yaml',
                           tmp_path / 'app.exe', gui=None)
    assert bridge.storage_handoff_state()['read_only'] is False
