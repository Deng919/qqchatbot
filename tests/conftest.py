import pytest


@pytest.fixture
def message_factory():
    from tests.factories import make_message

    return make_message
