"""Local, non-repository storage for the DeepSeek API key entered in the UI."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


_MAX_KEY_LENGTH = 4096


def _validate_key(value: str) -> str:
    if not value or len(value) > _MAX_KEY_LENGTH or any(char.isspace() for char in value):
        raise ValueError("API Key 必须是非空单行文本，且不能包含空白")
    return value


def _private_permissions(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return

    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        current_user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    principals = (
        current_user,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid),
    )
    inherit_flags = (
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        if directory else 0
    )
    acl = win32security.ACL()
    for sid in principals:
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inherit_flags,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid,
        )
    descriptor = win32security.GetFileSecurity(
        str(path), win32security.DACL_SECURITY_INFORMATION
    )
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )


def ui_api_key_exists(path: Path) -> bool:
    return path.is_file()


def read_ui_api_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8-sig").removesuffix("\n")
    return _validate_key(value)


def save_ui_api_key(path: Path, value: str) -> None:
    value = _validate_key(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _private_permissions(path.parent, directory=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".deepseek-key-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(value + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        _private_permissions(temporary_path, directory=False)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
