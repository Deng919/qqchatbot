"""Read short-lived image credentials from the running QQ main process.

Candidates remain in memory for the caller; this module never logs or stores them.
"""
from __future__ import annotations

import ctypes
import re
import time
from collections import Counter


_IMAGE_KEY = re.compile(rb'[ &?]rkey=([A-Za-z0-9_-]{30,200})(?![A-Za-z0-9_-])')


def _candidate_keys(data: bytes) -> list[str]:
    return [match.group(1).decode('ascii') for match in _IMAGE_KEY.finditer(data)]


def scan_image_keys(*, limit: int = 16, seconds: float = 10) -> list[str]:
    """Read only committed, readable memory in the verified QQ main process."""
    if type(limit) is not int or limit <= 0 or not isinstance(seconds, (int, float)) or not seconds > 0:
        return []
    from . import refresh

    deadline = time.monotonic() + min(seconds, 10)
    pid = refresh._find_qq_main_pid()
    # The existing finder can fall back to a renderer when no main process exists.
    if not pid or not refresh._pid_has_module(pid, 'wrapper.node'):
        return []
    kernel = refresh.windll.kernel32
    handle = kernel.OpenProcess(refresh.PROCESS_VM_READ | refresh.PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    counts: Counter[str] = Counter()
    mbi = refresh.MEMORY_BASIC_INFORMATION()
    address = 0
    try:
        while time.monotonic() < deadline:
            if not kernel.VirtualQueryEx(handle, ctypes.c_void_p(address), refresh.byref(mbi), refresh.sizeof(mbi)):
                break
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if (mbi.State == refresh.MEM_COMMIT and not mbi.Protect & 0x100
                    and (mbi.Protect & 0xFF) in refresh.READABLE_PROTS
                    and 0 < size <= refresh.MAX_REGION):
                data = refresh._read_memory(handle, base, size)
                if data:
                    counts.update(_candidate_keys(data))
            next_address = base + size
            if next_address <= address or next_address >= 0x7FFFFFFFFFFF:
                break
            address = next_address
    finally:
        kernel.CloseHandle(handle)
    return [key for key, _ in counts.most_common(limit)]
