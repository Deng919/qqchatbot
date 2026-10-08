import asyncio
import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from qq_digest.operations import OperationCoordinator
from qq_digest.storage_files import StorageBusy, activity_lock


def test_cancellation_keeps_activity_until_actual_worker_finishes(tmp_path):
    module = importlib.import_module('qq_digest.web.worker_threads')
    operations = OperationCoordinator(activity_root=lambda:tmp_path)
    started, release = threading.Event(), threading.Event()
    def worker():
        started.set()
        assert release.wait(5)
    async def scenario():
        async def task_body():
            with operations.claim('collect'): await module.guarded_to_thread(worker)
        task = asyncio.create_task(task_body())
        while not started.is_set(): await asyncio.sleep(0.001)
        try:
            task.cancel(); await asyncio.sleep(0.01)
            task.cancel(); await asyncio.sleep(0.01)
            assert operations.is_active('collect')
            with ThreadPoolExecutor(max_workers=1) as pool:
                def claim_elsewhere():
                    with activity_lock(tmp_path): pass
                with pytest.raises(StorageBusy): pool.submit(claim_elsewhere).result(timeout=1)
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
        assert not operations.is_active('collect')
    asyncio.run(scenario())


def test_guarded_worker_preserves_result_and_error():
    module = importlib.import_module('qq_digest.web.worker_threads')
    assert asyncio.run(module.guarded_to_thread(lambda a:a+1, 2)) == 3
    def fail(): raise ValueError('worker error')
    with pytest.raises(ValueError, match='worker error'): asyncio.run(module.guarded_to_thread(fail))


def test_cancel_all_async_tasks_does_not_release_physical_worker_claim(tmp_path):
    module = importlib.import_module('qq_digest.web.worker_threads')
    operations = OperationCoordinator(activity_root=lambda:tmp_path)
    started, release = threading.Event(), threading.Event()
    def worker():
        started.set()
        assert release.wait(5)
    async def scenario():
        async def body():
            with operations.claim('collect'): await module.guarded_to_thread(worker)
        caller = asyncio.create_task(body())
        while not started.is_set(): await asyncio.sleep(.001)
        try:
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task(): task.cancel()
            await asyncio.sleep(.02)
            assert operations.is_active('collect'), 'shutdown released the claim while thread remained active'
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError): await caller
    asyncio.run(scenario())
