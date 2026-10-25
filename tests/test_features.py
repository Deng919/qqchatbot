import pytest
from qq_digest.archive import Archive
from qq_digest.features import FeatureService, FeatureConflict


def test_switches_persist_and_do_not_delete_content(tmp_path):
    path = tmp_path / 'archive.sqlite'
    archive = Archive.open(path)
    service = FeatureService(archive)
    first = service.snapshot()
    assert all(value for key, value in first['values'].items() if key not in {'reminders', 'archive_qa'})
    assert first['values']['archive_qa'] is False
    assert first['values']['reminders'] is False
    result = service.update({'bookmarks': False, 'auto_daily': False}, first['revision'])
    assert result['values']['bookmarks'] is False
    archive.close()
    archive = Archive.open(path)
    try:
        service = FeatureService(archive)
        assert not service.enabled('bookmarks')
        with pytest.raises(FeatureConflict):
            service.update({'bookmarks': True}, first['revision'])
        assert not service.enabled('bookmarks')
        restored = service.update({'bookmarks': True}, result['revision'])
        assert restored['values']['bookmarks'] and not restored['values']['auto_daily']
    finally:
        archive.close()


@pytest.mark.parametrize('values', [{'bogus': True}, {'bookmarks': 'false'}, {'bookmarks': 0}, {}])
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
