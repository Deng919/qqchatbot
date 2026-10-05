"""Scan local reminder rules while the application is running."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .archive import Archive
from .features import FeatureService
from .operations import OperationBusy
from .reminders import ReminderService

logger = logging.getLogger(__name__)


async def run_reminder_check(database_path, operations, *, enabled, timezone_name='Asia/Shanghai', sink=None):
    if not enabled():
        return False

    def work():
        with operations.claim('reminder_mutation'):
            archive = Archive.open(Path(database_path))
            try:
                if not FeatureService(archive).enabled('reminders'):
                    return False
                service = ReminderService(archive, timezone_name=timezone_name)
                service.scan()
                if not FeatureService(archive).enabled('reminders'):
                    return False
                service.dispatch_windows(sink if sink and getattr(sink, 'available', False) else None)
                return True
            finally:
                archive.close()
    try:
        return await asyncio.to_thread(work)
    except OperationBusy:
        return False


async def reminder_loop(database_path, operations, *, enabled, timezone_name='Asia/Shanghai', sink_getter=lambda: None):
    if not database_path:
        return
    while True:
        try:
            await run_reminder_check(database_path, operations, enabled=enabled,
                timezone_name=timezone_name, sink=sink_getter())
        except Exception:
            # Do not copy sensitive exception bodies into desktop notifications.
            logger.exception('Local reminder check failed')
        await asyncio.sleep(60)
