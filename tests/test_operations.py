import pytest

from qq_digest.web.operations import OperationBusy, OperationCoordinator


@pytest.mark.parametrize(
    "first,second",
    [
        ("refresh", "collect"),
        ("collect", "daily"),
        ("sync", "refresh"),
        ("daily", "sync"),
        ("manual_summary", "daily"),
        ("refresh", "manual_summary"),
        ("manual_summary", "manual_summary"),
        ("manual_summary", "group_mutation"),
        ("history_inspection", "sync"),
        ("history_inspection", "group_mutation"),
        ("collect", "history_inspection"),
        ("setup_mutation", "backup"),
        ("backup", "setup_mutation"),
        ("version_mutation", "collect"),
        ("setup_mutation", "version_mutation"),
    ],
)
def test_conflicting_operations_are_rejected(first, second):
    operations = OperationCoordinator()
    with operations.claim(first):
        with pytest.raises(OperationBusy) as caught:
            with operations.claim(second):
                pass
        assert caught.value.active == (first,)


def test_claim_releases_state_after_exception():
    operations = OperationCoordinator()
    with pytest.raises(RuntimeError):
        with operations.claim("daily"):
            raise RuntimeError("boom")
    assert operations.snapshot() == {
        "daily_running": False,
        "refresh_running": False,
        "collect_running": False,
        "sync_running": False,
        "manual_summary_running": False,
        "group_mutation_running": False,
        "history_inspection_running": False,
        "knowledge_mutation_running": False,
        "backup_running": False,
        "storage_mutation_running": False,
        "topic_mutation_running": False,
        "reminder_mutation_running": False,
        "bookmark_mutation_running": False,
        "setup_mutation_running": False,
        "version_mutation_running": False,
        "archive_qa_running": False,
    }


def test_unknown_operation_is_rejected():
    operations = OperationCoordinator()

    with pytest.raises(ValueError, match="未知操作"):
        with operations.claim("unknown"):
            pass


@pytest.mark.parametrize('background',['daily','manual_summary'])
def test_preferences_work_during_summary_without_unlocking_storage(tmp_path,background):
    from concurrent.futures import ThreadPoolExecutor
    operations=OperationCoordinator(lambda:tmp_path)
    with operations.claim(background):
        def preference():
            with operations.claim('bookmark_mutation'):
                assert operations.is_active(background)
                for exclusive in ['backup','storage_mutation','version_mutation','group_mutation']:
                    with pytest.raises(OperationBusy):
                        with operations.claim(exclusive):pass
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(preference).result()


@pytest.mark.parametrize('exclusive',['backup','storage_mutation','version_mutation','collect','sync','archive_qa'])
def test_preferences_still_respect_exclusive_tasks(exclusive):
    operations=OperationCoordinator()
    with operations.claim(exclusive):
        with pytest.raises(OperationBusy):
            with operations.claim('bookmark_mutation'):pass


def test_other_coordinator_cannot_join_summary_lease(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    first=OperationCoordinator(lambda:tmp_path)
    second=OperationCoordinator(lambda:tmp_path)
    with first.claim('daily'):
        def unrelated():
            with pytest.raises(OperationBusy):
                with second.claim('bookmark_mutation'):pass
        with ThreadPoolExecutor(max_workers=1) as executor:executor.submit(unrelated).result()
