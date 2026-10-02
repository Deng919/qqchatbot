import pytest
from qq_digest.archive import Archive
from qq_digest.features import FeatureService, FeatureConflict


def test_switches_persist_and_do_not_delete_content(tmp_path):
    path = tmp_path / 'archive.sqlite'
    archive = Archive.open(path)
    service = FeatureService(archive)
    first = service.snapshot()
    assert all(first['values'].values())
    result = service.update({'tasks': False, 'auto_daily': False}, first['revision'])
    assert result['values']['tasks'] is False
    archive.close()
    archive = Archive.open(path)
    try:
        service = FeatureService(archive)
        assert not service.enabled('tasks')
        with pytest.raises(FeatureConflict):
            service.update({'tasks': True}, first['revision'])
        assert not service.enabled('tasks')
        restored = service.update({'tasks': True}, result['revision'])
        assert restored['values']['tasks'] and not restored['values']['auto_daily']
    finally:
        archive.close()


@pytest.mark.parametrize('values', [{'bogus': True}, {'tasks': 'false'}, {'tasks': 0}, {}])
def test_switches_reject_invalid_changes(tmp_path, values):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    try:
        with pytest.raises(ValueError):
            FeatureService(archive).update(values, 0)
    finally:
        archive.close()


def test_disabled_inspection_skips_source_access():
    import asyncio
    from qq_digest.web.history_inspection import run_scheduled_inspection
    # Invalid objects prove the disabled branch returns before touching source/config/coordinator.
    assert asyncio.run(run_scheduled_inspection(object(), object(), enabled=lambda: False)) is False
