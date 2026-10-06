"""Windows worker launched independently of the closing desktop window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from .desktop_releases import ReleaseRegistry
from .desktop_updates import STATE_DIR, atomic_json, read_transaction, run_transaction, signature


def process_alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x100000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise OSError('无法核实原进程状态')
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def visible_window(pid):
    user = ctypes.WinDLL('user32', use_last_error=True)
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    def callback(hwnd, extra):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            found.append(True)
        return True
    user.EnumWindows(callback_type(callback), 0)
    return bool(found)


def process_identity(pid, existing_handle=None):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = existing_handle or kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError('无法核实更新进程')
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
            raise OSError('无法核实进程启动时间')
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise OSError('无法核实进程路径')
        return {'path': buffer.value, 'created': (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}
    finally:
        if existing_handle is None:
            kernel.CloseHandle(handle)


class WindowsRuntime:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.children = {}

    def wait_parent(self, pid):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if not process_alive(pid):
                return True
            time.sleep(.25)
        return False

    def port_free(self, url):
        try:
            with urllib.request.urlopen(url + 'healthz', timeout=1):
                return False
        except OSError:
            return True

    def launch(self, record, transaction, *, recovery=False):
        executable = Path(record['path']) / 'QQDigestDesktop.exe'
        args = [str(executable)]
        if record['worker_protocol'] >= 1:
            args += ['--update-recover' if recovery else '--update-start', str(self.state_dir)]
        child = subprocess.Popen(args, cwd=record['path'], creationflags=subprocess.CREATE_NO_WINDOW)
        self.children[child.pid] = child
        return child.pid

    def confirm(self, child, record, transaction, *, recovery=False):
        deadline = time.monotonic() + 60
        ack_path = self.state_dir / 'startup.json'
        expected_signature = transaction.get('recovery_config_signature') if recovery else transaction['config_signature']
        while time.monotonic() < deadline:
            if self.children[child].poll() is not None:
                return False
            try:
                if record['worker_protocol'] >= 1:
                    ack = json.loads(ack_path.read_text(encoding='utf-8'))
                    if (ack.get('token') == transaction['token'] and ack.get('backend_id') == record['backend_id']
                            and ack.get('config_signature') == expected_signature
                            and ack.get('pid') == child and visible_window(child)):
                        return True
                elif visible_window(child):
                    from .config import load_config
                    config = load_config(Path(transaction['config_path']), create_dirs=False)
                    url = f'http://127.0.0.1:{config.web.port}/'
                    with urllib.request.urlopen(url + 'desktop-info', timeout=1) as response:
                        info = json.load(response)
                    if (info.get('backend_id') == record['backend_id'] and
                            info.get('config_signature') == expected_signature):
                        return True
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        return False

    def stop(self, child):
        process = self.children[child]
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)

    def identity(self, child):
        return process_identity(child)

    def stop_existing(self, pid, identity, record):
        if not process_alive(pid):
            return
        executable = Path(record['path']) / 'QQDigestDesktop.exe'
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x101001, False, pid)
        if not handle:
            raise RuntimeError('无法关闭上次启动的目标版本')
        try:
            actual = process_identity(pid, handle)
            if (not identity or actual != identity or Path(actual['path']).resolve() != executable.resolve()):
                raise RuntimeError('无法核实上次启动的进程，请先关闭目标版本')
            if not kernel.TerminateProcess(handle, 1) or kernel.WaitForSingleObject(handle, 15000) != 0:
                raise RuntimeError('目标版本尚未退出')
        finally:
            kernel.CloseHandle(handle)

    def switch_entries(self, transaction, target):
        import pythoncom
        import win32com.client
        from win32com.shell import shell, shellcon
        from .desktop_settings import auto_start_enabled, set_auto_start
        pythoncom.CoInitialize()
        changed = []
        source_exe = Path(transaction['source']['path']) / 'QQDigestDesktop.exe'
        target_exe = Path(target['path']) / 'QQDigestDesktop.exe'
        other_exe = Path(transaction['target']['path']) / 'QQDigestDesktop.exe'
        try:
            desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, 0, 0))
            shortcut_path = desktop / 'QQ Digest.lnk'
            if shortcut_path.is_file():
                shortcut = win32com.client.Dispatch('WScript.Shell').CreateShortcut(str(shortcut_path))
                if Path(shortcut.TargetPath).resolve() in {source_exe.resolve(), other_exe.resolve()}:
                    previous = (shortcut.TargetPath, shortcut.WorkingDirectory, shortcut.IconLocation)
                    shortcut.TargetPath = str(target_exe)
                    shortcut.WorkingDirectory = str(target_exe.parent)
                    shortcut.IconLocation = str(target_exe) + ',0'
                    shortcut.Save()
                    changed.append((shortcut, previous))
            if auto_start_enabled(source_exe) or auto_start_enabled(other_exe):
                set_auto_start(target_exe, True)
        except Exception:
            for shortcut, previous in reversed(changed):
                shortcut.TargetPath, shortcut.WorkingDirectory, shortcut.IconLocation = previous
                shortcut.Save()
            raise
        finally:
            pythoncom.CoUninitialize()


def acknowledge_start(state_dir, install_dir, config_path, backend_id, *, recovery=False):
    registry = ReleaseRegistry(Path(r'D:\Apps'), Path(state_dir))
    transaction = read_transaction(registry)
    record = transaction.get('source' if recovery else 'target', {})
    expected_signature = transaction.get('recovery_config_signature') if recovery else transaction.get('config_signature')
    if (transaction.get('status') != ('recovering' if recovery else 'starting') or
            Path(record.get('path', '')).resolve() != Path(install_dir).resolve() or
            record.get('backend_id') != backend_id or
            signature(Path(config_path)) != expected_signature):
        raise RuntimeError('更新启动校验失败')
    atomic_json(Path(state_dir) / 'startup.json', {
        'token': transaction['token'], 'backend_id': backend_id,
        'config_signature': expected_signature, 'pid': os.getpid(),
    })


def worker_main(state_dir):
    registry = ReleaseRegistry(Path(r'D:\Apps'), Path(state_dir))
    return run_transaction(registry, WindowsRuntime(state_dir))
