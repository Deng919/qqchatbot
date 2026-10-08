"""Keep outer operation claims alive until non-cancellable Python workers finish."""
import asyncio
import contextvars
from functools import partial


async def guarded_to_thread(function, *args, **kwargs):
    # Executor futures are not asyncio Tasks: loop shutdown cannot cancel the
    # physical completion signal by cancelling every pending coroutine task.
    context = contextvars.copy_context()
    worker = asyncio.get_running_loop().run_in_executor(None, context.run, partial(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A disconnected request or shutdown cannot stop a Python thread. Even
        # repeated cancellation must not release the caller's storage lease early.
        while not worker.done():
            try: await asyncio.shield(worker)
            except asyncio.CancelledError: continue
            except Exception: break
        if not worker.cancelled(): worker.exception()  # consume abandoned errors
        raise
