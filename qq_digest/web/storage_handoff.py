"""Runtime storage handoff status and an admission barrier for every HTTP write."""
from contextlib import nullcontext

from fastapi.responses import JSONResponse

from ..operations import OperationBusy

HANDOFF_MESSAGE = '存储位置已切换，请重启程序后继续'


def runtime_state(app):
    bridge = getattr(app.state, 'desktop_bridge', None)
    status = getattr(bridge, 'storage_handoff_state', None)
    return status() if callable(status) else {'read_only': False, 'restart_required': False, 'message': ''}


class StorageHandoffMiddleware:
    """Pure ASGI keeps admission alive until the actual request finishes on cancel."""
    def __init__(self, app, *, runtime):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope['path'].rstrip('/') or '/'
        # Version inventory registers a release on disk; it is an implicit write.
        write = scope['method'] not in {'GET', 'HEAD', 'OPTIONS'} or path == '/api/desktop-updates'
        # Authentication only issues/clears a cookie; it does not mutate storage.
        exempt = path in {'/login', '/logout', '/api/desktop-session'}
        state = runtime_state(self.runtime)
        scope.setdefault('state', {})['storage_handoff'] = state
        try:
            with self.runtime.state.operations.request_write() if write and not exempt else nullcontext():
                await self.app(scope, receive, send)
        except OperationBusy as exc:
            response = JSONResponse({'detail': str(exc), 'restart_required': runtime_state(self.runtime)['restart_required']}, status_code=409)
            await response(scope, receive, send)


def add_storage_handoff(app, *, require_login):
    from fastapi import Request
    app.state.operations.blocker = lambda: runtime_state(app)['message'] or None
    app.add_middleware(StorageHandoffMiddleware, runtime=app)

    @app.get('/api/runtime-state')
    async def state(request: Request):
        require_login(request)
        return runtime_state(app)


async def run_notification_queue(config, operations, notifier):
    """Own the archive and claim in the physical worker, through cancellation."""
    from ..archive import Archive
    from ..notify import NotificationQueueProcessor
    from .worker_threads import guarded_to_thread
    def work():
        with operations.claim('knowledge_mutation'):
            archive = Archive.open(config.archive_path)
            try:
                return NotificationQueueProcessor(archive=archive, notifier=notifier,
                    max_attempts=config.qq_bot.max_retries,
                    retry_base_seconds=config.qq_bot.retry_base_seconds).process_due()
            finally:
                archive.close()
    try:
        return await guarded_to_thread(work)
    except OperationBusy:
        return None
