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
    }


def test_unknown_operation_is_rejected():
    operations = OperationCoordinator()

    with pytest.raises(ValueError, match="未知操作"):
        with operations.claim("unknown"):
            pass
