import ctypes
from types import SimpleNamespace

from qq_digest import refresh


def test_candidates_respect_character_and_length_bounds_and_duplicate_counts():
    from qq_digest.qq_image_auth import _candidate_keys
    data = (b'?rkey=' + b'A' * 30 + b'&rkey=' + b'B' * 200
        + b' rkey=' + b'A' * 30 + b'&rkey=' + b'C' * 29
        + b'&rkey=' + b'D' * 201 + b'\0xrkey=' + b'E' * 30)
    assert _candidate_keys(data) == ['A' * 30, 'B' * 200, 'A' * 30]


def fake_process(monkeypatch, regions, *, main=True, opened=True):
    from qq_digest import qq_image_auth
    closed = []
    read_calls = []
    def query(handle, address, pointer, size):
        assert handle == 77
        start = address.value or 0
        if start >= len(regions) * 4096:
            return 0
        index = start // 4096
        mbi = ctypes.cast(pointer, ctypes.POINTER(refresh.MEMORY_BASIC_INFORMATION)).contents
        mbi.BaseAddress, mbi.RegionSize = index * 4096, 4096
        mbi.State, mbi.Protect = refresh.MEM_COMMIT, regions[index][0]
        return size
    def read(handle, address, size):
        assert handle == 77
        read_calls.append((address, size))
        return regions[address // 4096][1]
    def open_process(access, inherit, pid):
        assert access == refresh.PROCESS_VM_READ | refresh.PROCESS_QUERY_INFORMATION
        assert inherit is False and pid == 42
        return 77 if opened else 0
    monkeypatch.setattr(refresh, '_find_qq_main_pid', lambda: 42)
    monkeypatch.setattr(refresh, '_pid_has_module', lambda pid, name: main and pid == 42 and name == 'wrapper.node')
    monkeypatch.setattr(refresh, '_read_memory', read)
    monkeypatch.setattr(refresh, 'windll', SimpleNamespace(kernel32=SimpleNamespace(
        OpenProcess=open_process, VirtualQueryEx=query, CloseHandle=closed.append)))
    return qq_image_auth, read_calls, closed


def test_scan_deduplicates_and_ranks_candidates_without_reading_guard_pages(monkeypatch):
    auth, reads, closed = fake_process(monkeypatch, [
        (0x04, b'?rkey=' + b'A' * 30 + b'&rkey=' + b'B' * 30),
        (0x104, b'?rkey=' + b'C' * 30),
        (0x04, b'&rkey=' + b'B' * 30)])
    assert auth.scan_image_keys(limit=1) == ['B' * 30]
    assert reads == [(0, 4096), (8192, 4096)]
    assert closed == [77]


def test_scanner_refuses_fallback_non_main_process_and_missing_access(monkeypatch):
    auth, reads, closed = fake_process(monkeypatch, [(0x04, b'')], main=False)
    assert auth.scan_image_keys() == []
    assert reads == [] and closed == []
    auth, reads, closed = fake_process(monkeypatch, [(0x04, b'')], opened=False)
    assert auth.scan_image_keys() == []
    assert reads == [] and closed == []


def test_scan_budget_is_capped_at_ten_seconds(monkeypatch):
    auth, reads, closed = fake_process(monkeypatch, [(0x04, b'?rkey=' + b'A' * 30)] * 3)
    times = iter([0, 0, 11])
    monkeypatch.setattr(auth.time, 'monotonic', lambda: next(times))
    assert auth.scan_image_keys(seconds=500) == ['A' * 30]
    assert len(reads) == 1 and closed == [77]


def test_invalid_empty_budgets_never_read_memory(monkeypatch):
    auth, reads, closed = fake_process(monkeypatch, [(0x04, b'')])
    assert auth.scan_image_keys(limit=0) == []
    assert auth.scan_image_keys(seconds=0) == []
    assert reads == [] and closed == []


def test_explicit_candidate_limit_is_honored(monkeypatch):
    data = b''.join(b'?rkey=' + bytes([65 + index]) * 30 for index in range(17))
    auth, _, _ = fake_process(monkeypatch, [(0x04, data)])
    assert len(auth.scan_image_keys(limit=17)) == 17
