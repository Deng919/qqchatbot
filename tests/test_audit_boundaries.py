import pytest

from qq_digest.web.operations import OperationBusy, OperationCoordinator
from tests.test_web import web_client  # noqa: F401


@pytest.mark.parametrize('kind', ['backup', 'storage_mutation', 'topic_mutation'])
def test_new_data_operations_conflict_with_publication(kind):
    operations = OperationCoordinator()
    with operations.claim('daily'):
        with pytest.raises(OperationBusy):
            with operations.claim(kind):
                pass
    with operations.claim(kind):
        with pytest.raises(OperationBusy):
            with operations.claim('knowledge_mutation'):
                pass


def test_undo_knowledge_respects_running_publication(web_client):
    client, candidate_id, _ = web_client
    client.post('/login', data={'password': 'password123'})
    assert client.post(f'/candidates/{candidate_id}/confirm').status_code == 200
    with client.app.state.operations.claim('daily'):
        response = client.post(f'/api/candidates/{candidate_id}/undo')
        assert response.status_code == 409
    assert client.get(f'/api/candidates/{candidate_id}').json()['status'] == 'confirmed'
    assert client.post(f'/api/candidates/{candidate_id}/undo').status_code == 200


@pytest.mark.parametrize('url,body', [
    ('/api/desktop-settings/backup', None),
    ('/api/desktop-settings/migrate', {'destination': 'D:/Dev/SyntheticMigration'}),
    ('/api/desktop-settings/restore', {'path': 'D:/Cache/SyntheticBackup.zip',
      'destination': 'D:/Dev/SyntheticRestore', 'sha256': 'a' * 64}),
])
def test_desktop_mutations_do_not_start_during_daily(web_client, url, body):
    client, _, _ = web_client
    class Bridge:
        def backup_data(self):
            pytest.fail('Backup must not start while daily publishes')
        def migrate_storage(self, *args):
            pytest.fail('Migration must not start while daily publishes')
        def restore_backup(self, *args):
            pytest.fail('Restore must not start while daily publishes')
    client.app.state.desktop_bridge = Bridge()
    client.post('/login', data={'password': 'password123'})
    with client.app.state.operations.claim('daily'):
        assert client.post(url, json=body).status_code == 409
