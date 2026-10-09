from __future__ import annotations

from contextlib import contextmanager, nullcontext
from threading import Lock
from contextvars import ContextVar


OPERATION_KINDS = (
    "daily", "refresh", "collect", "sync", "manual_summary", "group_mutation", "history_inspection", "knowledge_mutation",
    "backup", "storage_mutation", "topic_mutation", "reminder_mutation", "bookmark_mutation", "setup_mutation", "version_mutation", "archive_qa"
)
CONFLICTS = {kind: frozenset(OPERATION_KINDS) for kind in OPERATION_KINDS}


class OperationBusy(RuntimeError):
    def __init__(self, requested: str, active: tuple[str, ...]):
        super().__init__(
            "有任务正在运行，请稍后重试"
        )
        self.requested = requested
        self.active = active


class OperationBlocked(OperationBusy):
    def __init__(self, requested: str, message: str):
        super().__init__(requested, ('storage_handoff',))
        self.args = (message,)


class OperationCoordinator:
    def __init__(self, activity_root=None):
        self._active: set[str] = set()
        self._lock = Lock()
        self.activity_root = activity_root
        self.blocker = None
        self._requests: set[object] = set()
        self._request_token = ContextVar('operation_request', default=None)

    def _check_blocker(self, kind):
        message = self.blocker() if self.blocker is not None else None
        if message:
            raise OperationBlocked(kind, message)

    @contextmanager
    def request_write(self):
        """Admit HTTP writes atomically with storage claims, including unclaimed APIs."""
        token = object()
        with self._lock:
            self._check_blocker('http_write')
            active = tuple(sorted(self._active & {'storage_mutation', 'version_mutation'}))
            if active:
                raise OperationBusy('http_write', active)
            self._requests.add(token)
        previous = self._request_token.set(token)
        try:
            yield
        finally:
            self._request_token.reset(previous)
            with self._lock:
                self._requests.discard(token)

    @contextmanager
    def claim(self, kind: str):
        if kind not in CONFLICTS:
            raise ValueError(f"未知操作: {kind}")
        with self._lock:
            self._check_blocker(kind)
            active = tuple(sorted(self._active & CONFLICTS[kind]))
            if kind in {'storage_mutation', 'version_mutation'} and self._requests - {self._request_token.get()}:
                active += ('http_write',)
            if active:
                raise OperationBusy(kind, active)
            self._active.add(kind)
        try:
            from .storage_files import StorageBusy, activity_lock
            root = self.activity_root() if self.activity_root is not None else None
            try:
                with activity_lock(root) if root is not None else nullcontext():
                    yield
            except StorageBusy as exc:
                raise OperationBusy(kind, ('external',)) from exc
        finally:
            with self._lock:
                self._active.discard(kind)

    def can_start(self, kind: str) -> bool:
        if kind not in CONFLICTS:
            raise ValueError(f"未知操作: {kind}")
        with self._lock:
            try:
                self._check_blocker(kind)
            except OperationBusy:
                return False
            if kind in {'storage_mutation', 'version_mutation'} and self._requests - {self._request_token.get()}:
                return False
            return not bool(self._active & CONFLICTS[kind])

    def is_active(self, kind: str) -> bool:
        if kind not in CONFLICTS:
            raise ValueError(f"未知操作: {kind}")
        with self._lock:
            return kind in self._active

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return {
                f"{kind}_running": kind in self._active
                for kind in OPERATION_KINDS
            }
