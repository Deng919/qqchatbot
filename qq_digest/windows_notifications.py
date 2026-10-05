"""A transient Windows notification owned by a hidden window on its own thread."""
from __future__ import annotations

import ctypes
import sys
import threading
import time


def _truncate(value, units):
    return str(value).encode('utf-16-le')[:units * 2].decode('utf-16-le', errors='ignore')


class _WindowsAPI:
    def __init__(self):
        from ctypes import wintypes as w

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', ctypes.c_ubyte * 8)]

        class Data(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT),
                        ('uFlags', w.UINT), ('uCallbackMessage', w.UINT), ('hIcon', w.HICON),
                        ('szTip', w.WCHAR * 128), ('dwState', w.DWORD), ('dwStateMask', w.DWORD),
                        ('szInfo', w.WCHAR * 256), ('uVersion', w.UINT), ('szInfoTitle', w.WCHAR * 64),
                        ('dwInfoFlags', w.DWORD), ('guidItem', GUID), ('hBalloonIcon', w.HICON)]

        self.w, self.Data = w, Data
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        self.user.CreateWindowExW.restype = w.HWND
        self.user.LoadIconW.argtypes = [w.HINSTANCE, w.LPCWSTR]
        self.user.LoadIconW.restype = w.HICON
        self.user.DestroyWindow.argtypes = [w.HWND]
        self.user.DestroyWindow.restype = w.BOOL
        self.user.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        self.user.PeekMessageW.restype = w.BOOL
        self.user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        self.user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        self.user.DispatchMessageW.restype = w.LPARAM
        self.shell.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(Data)]
        self.shell.Shell_NotifyIconW.restype = w.BOOL

    def create(self):
        window = self.user.CreateWindowExW(0, 'STATIC', 'QQ Digest reminder', 0, 0, 0, 0, 0, None, None, None, None)
        if not window:
            raise ctypes.WinError(ctypes.get_last_error())
        data = self.Data()
        data.cbSize, data.hWnd, data.uID = ctypes.sizeof(data), window, 1
        data.uFlags = 2 | 4
        data.hIcon = self.user.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32516), self.w.LPCWSTR))
        data.szTip = 'QQ Digest 提醒'
        return data

    def add(self, data):
        return bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(data)))

    def version(self, data):
        data.uVersion = 4
        return bool(self.shell.Shell_NotifyIconW(4, ctypes.byref(data)))

    def show(self, data, title, body):
        data.uFlags = 0x10
        data.szInfoTitle, data.szInfo = title, body
        data.dwInfoFlags = 1 | 0x80  # Information, respecting Windows quiet time.
        return bool(self.shell.Shell_NotifyIconW(1, ctypes.byref(data)))

    def pump(self):
        msg = self.w.MSG()
        while self.user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            self.user.TranslateMessage(ctypes.byref(msg))
            self.user.DispatchMessageW(ctypes.byref(msg))

    def delete(self, data):
        self.shell.Shell_NotifyIconW(2, ctypes.byref(data))

    def destroy(self, data):
        self.user.DestroyWindow(data.hWnd)


class WindowsNotificationSink:
    """Returns system acceptance; Windows settings still control visible display."""
    def __init__(self, *, native_factory=None, startup_timeout=3):
        self._factory = native_factory or _WindowsAPI
        self._supported = native_factory is not None or sys.platform == 'win32'
        self._closed = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._startup_timeout = startup_timeout

    @property
    def available(self):
        return self._supported and not self._closed

    def __call__(self, title, body):
        ready = threading.Event()
        result = [False]
        with self._lock:
            if not self.available or (self._thread and self._thread.is_alive()):
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._display,
                args=(_truncate(title, 63), _truncate(body, 255), ready, result), daemon=True)
            self._thread.start()
        if not ready.wait(self._startup_timeout):
            self._stop.set()
            return None  # Request outcome is uncertain; do not auto-resubmit.
        return result[0]

    def _display(self, title, body, ready, result):
        native = handle = None
        added = False
        try:
            native = self._factory()
            handle = native.create()
            added = native.add(handle)
            if added and native.version(handle) and not self._stop.is_set():
                result[0] = native.show(handle, title, body)
            ready.set()
            deadline = time.monotonic() + 30
            while result[0] and time.monotonic() < deadline and not self._stop.wait(.1):
                native.pump()
        except Exception:
            result[0] = False
        finally:
            ready.set()
            if native is not None and handle is not None:
                try:
                    if added:
                        native.delete(handle)
                finally:
                    native.destroy(handle)

    def close(self):
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self._thread
        if thread:
            thread.join(3)
