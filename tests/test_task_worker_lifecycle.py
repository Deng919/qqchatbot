"""Cancelled task decisions must finish before storage can be switched."""
import asyncio
from threading import Event

import httpx
import pytest

from qq_digest.operations import OperationBusy
from qq_digest.task_inbox import TaskInboxService
from tests.test_storage_handoff import runtime


def test_cancelled_task_decision_retains_storage_admission(runtime, monkeypatch):
    client, bridge, root = runtime
    entered, release = Event(), Event()

    def decide(self, *args, **kwargs):
        entered.set()
        release.wait(5)
        return None

    monkeypatch.setattr(TaskInboxService, 'decide', decide)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                    base_url='http://testserver', cookies=client.cookies) as http:
            task = asyncio.create_task(http.post('/api/tasks/suggestions/synthetic/decision',
                                                 json={'action': 'confirm', 'title': '合成待办'}))
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
