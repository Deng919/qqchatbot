from __future__ import annotations

from contextlib import contextmanager
from threading import Lock


OPERATION_KINDS = (
    "daily", "refresh", "collect", "sync", "manual_summary", "group_mutation"
)
CONFLICTS = {kind: frozenset(OPERATION_KINDS) for kind in OPERATION_KINDS}


class OperationBusy(RuntimeError):
    def __init__(self, requested: str, active: tuple[str, ...]):
        super().__init__(
            f"操作 {requested} 与正在运行的 {', '.join(active)} 冲突"
        )
        self.requested = requested
        self.active = active


class OperationCoordinator:
    def __init__(self):
        self._active: set[str] = set()
        self._lock = Lock()

    @contextmanager
    def claim(self, kind: str):
        if kind not in CONFLICTS:
            raise ValueError(f"未知操作: {kind}")
        with self._lock:
            active = tuple(sorted(self._active & CONFLICTS[kind]))
            if active:
                raise OperationBusy(kind, active)
            self._active.add(kind)
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(kind)

    def can_start(self, kind: str) -> bool:
        if kind not in CONFLICTS:
            raise ValueError(f"未知操作: {kind}")
        with self._lock:
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
