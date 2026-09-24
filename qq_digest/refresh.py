"""NTQQ 数据库自动刷新模块。

从运行中的 QQ 进程内存提取密钥，解密最新的 NTQQ 数据库到本地。
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac as hmac_mod
import logging
import os
import shutil
import sqlite3
import struct
import time
import tempfile
from ctypes import windll, byref, sizeof, create_string_buffer
from ctypes.wintypes import DWORD, HMODULE
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

# SQLCipher constants (QQ NT custom format)
EXT_HEADER = 1024
PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SIZE = 20
RESERVE = 48
HMAC_MASK = 0x3A
KDF_ITER = 4000
FAST_ITER = 2
PRE_LOGIN_KEY = b"BD156D6710D54D8782F4"

# Windows API constants
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MEM_COMMIT = 0x1000
READABLE_PROTS = {0x02, 0x04, 0x06, 0x08, 0x20, 0x40, 0x60, 0x80}
MAX_REGION = 256 * 1024 * 1024
HEX_CHARS = set(b"0123456789abcdefABCDEF")


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", DWORD),
        ("Protect", DWORD),
        ("Type", DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", DWORD),
        ("cntThreads", DWORD),
        ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("th32ModuleID", DWORD),
        ("th32ProcessID", DWORD),
        ("GlblcntUsage", DWORD),
        ("ProccntUsage", DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", DWORD),
        ("hModule", HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * 260),
    ]


@dataclass
class RefreshResult:
    success: bool
    message: str = ""
    files_decrypted: int = 0
    files_failed: int = 0
    output_dir: str = ""
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class SnapshotError(RuntimeError):
    pass


def _copy_stable_snapshot(
    source: str | Path,
    target: str | Path,
    *,
    retries: int = 3,
    retry_delay: float = 0.25,
) -> Path:
    """Copy a live QQ database only when its source metadata stays stable."""
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        before = source.stat()
        shutil.copy2(source, target)
        after = source.stat()
        copied = target.stat()
        if (
            before.st_size == after.st_size == copied.st_size
            and before.st_mtime_ns == after.st_mtime_ns
        ):
            return target
        target.unlink(missing_ok=True)
        if attempt + 1 < retries:
            time.sleep(retry_delay)
    raise SnapshotError(f"数据库复制期间持续变化: {source.name}")


def _copy_database_snapshot(
    source: str | Path,
    target: str | Path,
    *,
    retries: int = 3,
    retry_delay: float = 0.25,
) -> Path:
    """Copy a database and its WAL only when both remain unchanged."""
    source = Path(source)
    target = Path(target)
    source_wal = Path(str(source) + "-wal")
    target_wal = Path(str(target) + "-wal")
    target.parent.mkdir(parents=True, exist_ok=True)

    def state(path: Path) -> tuple[bool, int, int]:
        try:
            stat = path.stat()
            return True, stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return False, 0, 0

    def header(path: Path) -> bytes:
        try:
            with path.open("rb") as handle:
                return handle.read(24)
        except FileNotFoundError:
            return b""

    def copy_prefix(path: Path, destination: Path, size: int) -> int:
        copied = 0
        with path.open("rb") as source_handle, destination.open("wb") as target_handle:
            while copied < size:
                chunk = source_handle.read(min(1024 * 1024, size - copied))
                if not chunk:
                    break
                target_handle.write(chunk)
                copied += len(chunk)
        return copied

    for attempt in range(retries):
        before_db = state(source)
        before_wal = state(source_wal)
        before_wal_header = header(source_wal) if before_wal[0] else b""
        shutil.copy2(source, target)
        if before_wal[0]:
            copied_wal_size = copy_prefix(source_wal, target_wal, before_wal[1])
        else:
            target_wal.unlink(missing_ok=True)
            copied_wal_size = 0
        after_db = state(source)
        after_wal = state(source_wal)
        after_wal_header = header(source_wal) if after_wal[0] else b""
        copied_db = state(target)
        wal_generation_stable = (
            not before_wal[0]
            and not after_wal[0]
            or before_wal[0]
            and after_wal[0]
            and after_wal[1] >= before_wal[1]
            and before_wal_header == after_wal_header
            and copied_wal_size == before_wal[1]
        )
        if (
            before_db == after_db
            and copied_db[1] == before_db[1]
            and wal_generation_stable
        ):
            return target
        target.unlink(missing_ok=True)
        target_wal.unlink(missing_ok=True)
        if attempt + 1 < retries:
            time.sleep(retry_delay)
    raise SnapshotError(f"数据库或 WAL 复制期间持续变化: {source.name}")


def _publish_staged_outputs(
    staged_dir: str | Path,
    output_dir: str | Path,
    *,
    refreshed_names: set[str],
    required_files: set[str],
) -> list[str]:
    """Publish a refresh only after every core database validated."""
    missing = sorted(required_files - {name.lower() for name in refreshed_names})
    if missing:
        return missing
    staged_dir = Path(staged_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in staged_dir.iterdir():
        if path.is_file() and path.name.lower() in refreshed_names:
            os.replace(path, output_dir / path.name)
    return []


def _required_refresh_files(available_files: set[str]) -> set[str]:
    """Choose the databases required by the collector's active read path."""
    required = {"group_info.db"}
    if "group_msg_fts.db" in available_files:
        required.add("group_msg_fts.db")
    else:
        required.add("nt_msg.db")
    return required


def _find_qq_pids() -> list[int]:
    """Find all QQ.exe process IDs."""
    snapshot = windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (ctypes.c_void_p(-1).value, -1):
        return []
    pe = PROCESSENTRY32()
    pe.dwSize = sizeof(PROCESSENTRY32)
    pids: list[int] = []
    if windll.kernel32.Process32First(snapshot, byref(pe)):
        while True:
            name = pe.szExeFile.decode("utf-8", errors="replace").lower()
            if name == "qq.exe":
                pids.append(pe.th32ProcessID)
            if not windll.kernel32.Process32Next(snapshot, byref(pe)):
                break
    windll.kernel32.CloseHandle(snapshot)
    return pids


def _pid_has_module(pid: int, module_name: str) -> bool:
    """Check if a process has a specific module loaded."""
    snapshot = windll.kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot in (ctypes.c_void_p(-1).value, -1, 0):
        return False
    me = MODULEENTRY32()
    me.dwSize = sizeof(MODULEENTRY32)
    found = False
    if windll.kernel32.Module32First(snapshot, byref(me)):
        while True:
            mod = me.szModule.decode("utf-8", errors="replace").lower()
            if mod == module_name.lower():
                found = True
                break
            if not windll.kernel32.Module32Next(snapshot, byref(me)):
                break
    windll.kernel32.CloseHandle(snapshot)
    return found


def _find_qq_main_pid() -> int | None:
    """Find the QQ main process (has wrapper.node module)."""
    pids = _find_qq_pids()
    if not pids:
        return None
    for pid in pids:
        if _pid_has_module(pid, "wrapper.node"):
            return pid
    return pids[0]


def _read_memory(handle: int, addr: int, size: int) -> bytes | None:
    """Read process memory."""
    buf = create_string_buffer(size)
    n = ctypes.c_size_t(0)
    if windll.kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, byref(n)):
        return buf.raw[: n.value]
    return None


def _derive_enc_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, KEY_SIZE)


def _derive_hmac_key(enc_key: bytes, salt: bytes) -> bytes:
    hmac_salt = bytes(b ^ HMAC_MASK for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, hmac_salt, FAST_ITER, KEY_SIZE)


def _verify_key_hmac(page1: bytes, enc_key: bytes) -> bool:
    """Validate enc_key against page 1 HMAC-SHA1."""
    salt = page1[:SALT_SIZE]
    hmac_key = _derive_hmac_key(enc_key, salt)
    data_end = PAGE_SIZE - RESERVE
    content = page1[SALT_SIZE:data_end]
    iv = page1[data_end : data_end + IV_SIZE]
    stored = page1[data_end + IV_SIZE : data_end + IV_SIZE + HMAC_SIZE]
    computed = hmac_mod.new(hmac_key, content + iv + struct.pack("<I", 1), hashlib.sha1).digest()
    return hmac_mod.compare_digest(computed, stored)


def _decrypt_page(page_data: bytes, enc_key: bytes, skip_salt: int = 0) -> bytes:
    """Decrypt a single SQLCipher page (AES-256-CBC)."""
    data = page_data[skip_salt:]
    encrypted = data[: len(data) - RESERVE]
    iv = data[len(data) - RESERVE : len(data) - RESERVE + IV_SIZE]
    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
    dec = cipher.decryptor()
    return dec.update(encrypted) + dec.finalize()


def _decrypt_page_image(
    page_data: bytes, enc_key: bytes, page_number: int
) -> bytes:
    decrypted = _decrypt_page(
        page_data,
        enc_key,
        skip_salt=SALT_SIZE if page_number == 1 else 0,
    )
    if page_number == 1:
        page = bytearray(b"SQLite format 3\x00" + decrypted)
        page[16:18] = struct.pack(">H", PAGE_SIZE)
        return bytes(page[:PAGE_SIZE]).ljust(PAGE_SIZE, b"\x00")
    return decrypted.ljust(PAGE_SIZE, b"\x00")


def _apply_encrypted_wal(
    wal_path: str | Path, output_path: str | Path, enc_key: bytes
) -> int:
    """Apply encrypted WAL frames through the last committed transaction."""
    wal_path = Path(wal_path)
    if not wal_path.exists() or wal_path.stat().st_size < 32:
        return 0
    data = wal_path.read_bytes()
    magic, _version, page_size, _sequence, salt1, salt2, _c1, _c2 = struct.unpack(
        ">8I", data[:32]
    )
    if magic not in {0x377F0682, 0x377F0683} or page_size != PAGE_SIZE:
        raise sqlite3.DatabaseError("unsupported WAL header")

    frame_size = 24 + page_size
    frames: list[tuple[int, int, bytes]] = []
    offset = 32
    while offset + frame_size <= len(data):
        header = struct.unpack(">6I", data[offset : offset + 24])
        page_number, commit_size, frame_salt1, frame_salt2, _fc1, _fc2 = header
        if frame_salt1 != salt1 or frame_salt2 != salt2 or page_number < 1:
            break
        payload = data[offset + 24 : offset + frame_size]
        frames.append((page_number, commit_size, payload))
        offset += frame_size

    last_commit = -1
    database_pages = 0
    for index, (_page_number, commit_size, _payload) in enumerate(frames):
        if commit_size:
            last_commit = index
            database_pages = commit_size
    if last_commit < 0:
        return 0

    with Path(output_path).open("r+b") as output:
        for page_number, _commit_size, payload in frames[: last_commit + 1]:
            page = _decrypt_page_image(payload, enc_key, page_number)
            output.seek((page_number - 1) * PAGE_SIZE)
            output.write(page)
        output.truncate(database_pages * PAGE_SIZE)
    return last_commit + 1


def _decrypt_db(
    input_path: str | Path,
    output_path: str | Path,
    enc_key: bytes,
    *,
    required_tables: set[str] | None = None,
    check_integrity: bool = False,
) -> bool:
    """Decrypt a full QQ NT database to plain SQLite."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.stat().st_size < EXT_HEADER + PAGE_SIZE:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temp_path = Path(temp_name)
    try:
        with open(input_path, "rb") as source, os.fdopen(fd, "wb") as target:
            source.seek(EXT_HEADER)
            pgno = 0
            while True:
                page_raw = source.read(PAGE_SIZE)
                if not page_raw:
                    break
                if len(page_raw) != PAGE_SIZE:
                    raise sqlite3.DatabaseError("truncated encrypted page")
                pgno += 1
                target.write(_decrypt_page_image(page_raw, enc_key, pgno))

        applied_frames = _apply_encrypted_wal(
            Path(str(input_path) + "-wal"), temp_path, enc_key
        )
        if applied_frames:
            logger.info("Applied %d WAL frames for %s", applied_frames, input_path.name)

        conn = sqlite3.connect(str(temp_path))
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        finally:
            conn.close()
        table_names = {row[0] for row in tables}
        if not table_names:
            return False
        if required_tables and not required_tables.issubset(table_names):
            return False
        if check_integrity:
            conn = sqlite3.connect(str(temp_path))
            try:
                integrity = conn.execute("PRAGMA quick_check").fetchall()
            finally:
                conn.close()
            if integrity != [("ok",)]:
                logger.warning(
                    "SQLite quick_check failed for %s: %s",
                    input_path.name,
                    integrity[:10],
                )
                return False
        os.replace(temp_path, output_path)
        return True
    except Exception as exc:
        logger.warning("Database decryption failed for %s: %s", input_path.name, exc)
        return False
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _collect_db_info(db_dirs: list[tuple[str, str]]) -> tuple[list, dict]:
    """Read encrypted DB headers, extract salt for key matching."""
    global_dbs = []
    pa_map: dict[str, list] = {}
    for account, d in db_dirs:
        for fn in os.listdir(d):
            if not fn.lower().endswith(".db"):
                continue
            fp = os.path.join(d, fn)
            try:
                with open(fp, "rb") as f:
                    hdr = f.read(EXT_HEADER + PAGE_SIZE)
                if len(hdr) < EXT_HEADER + PAGE_SIZE:
                    continue
                if hdr[:16] != b"SQLite header 3\x00":
                    continue
                page1 = hdr[EXT_HEADER : EXT_HEADER + PAGE_SIZE]
                salt_hex = page1[:SALT_SIZE].hex()
                if account == "global":
                    global_dbs.append((account, fp, page1))
                else:
                    pa_map.setdefault(salt_hex, []).append((account, fp, page1))
            except (OSError, PermissionError):
                continue
    return global_dbs, pa_map


def _scan_by_salt(handle: int, salt_hexes: list[str]) -> dict[str, tuple[int, str]]:
    """Scan process memory for key patterns matching known salts."""
    needles = [(sh.encode("ascii"), sh) for sh in salt_hexes]
    results: dict[str, tuple[int, str]] = {}
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    while True:
        ret = windll.kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), byref(mbi), sizeof(mbi))
        if ret == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if (
            mbi.State == MEM_COMMIT
            and (mbi.Protect & 0xFF) in READABLE_PROTS
            and 0 < size <= MAX_REGION
        ):
            data = _read_memory(handle, base, size)
            if data:
                for needle, sh in needles:
                    if sh in results:
                        continue
                    off = 0
                    while True:
                        idx = data.find(needle, off)
                        if idx == -1:
                            break
                        if idx >= 66 and idx + 33 <= len(data):
                            if (
                                data[idx - 66 : idx - 64] == b"x'"
                                and data[idx + 32 : idx + 33] == b"'"
                                and all(c in HEX_CHARS for c in data[idx - 64 : idx])
                            ):
                                key_hex = data[idx - 64 : idx].decode("ascii").lower()
                                results[sh] = (base + idx - 66, key_hex)
                                break
                        off = idx + 1
        nxt = base + size
        if nxt <= addr or nxt >= 0x7FFFFFFFFFFF:
            break
        addr = nxt
    return results


def refresh_database(
    *,
    qq_number: str | int,
    output_dir: str | Path,
    snapshot_root: str | Path | None = None,
    pid: int = 0,
) -> RefreshResult:
    """Main entry: extract keys from QQ memory and decrypt databases."""
    start = time.time()
    qq_number = str(qq_number)
    output_dir = Path(output_dir)
    errors: list[str] = []

    # 1. Find QQ main process
    main_pid = pid or _find_qq_main_pid()
    if not main_pid:
        return RefreshResult(False, "QQ 未运行，请先启动 QQ", errors=["no_process"])

    # 2. Discover encrypted DB directories
    nt_base = os.path.expandvars(r"%USERPROFILE%\Documents\Tencent Files")
    db_dirs = []
    global_dir = os.path.join(nt_base, "nt_qq", "global", "nt_db")
    if os.path.isdir(global_dir):
        db_dirs.append(("global", global_dir))
    personal_dir = os.path.join(nt_base, qq_number, "nt_qq", "nt_db")
    if os.path.isdir(personal_dir):
        db_dirs.append((qq_number, personal_dir))
    else:
        return RefreshResult(False, f"数据库目录不存在: {personal_dir}", errors=["no_db_dir"])

    global_dbs, pa_map = _collect_db_info(db_dirs)
    if not global_dbs and not pa_map:
        return RefreshResult(False, "未找到加密数据库", errors=["no_encrypted_db"])

    # 3. Global DBs: fixed key
    global_results: dict[str, str] = {}
    for _account, _fp, page1 in global_dbs:
        salt = page1[:SALT_SIZE]
        enc_key = _derive_enc_key(PRE_LOGIN_KEY, salt)
        if _verify_key_hmac(page1, enc_key):
            global_results[salt.hex()] = enc_key.hex()

    # 4. Personal DBs: scan memory
    personal_results: dict[str, str] = {}
    if pa_map:
        handle = windll.kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, main_pid
        )
        if not handle:
            return RefreshResult(
                False, f"无法访问 QQ 进程 (PID {main_pid})，需要管理员权限",
                errors=["open_process_failed"],
            )
        try:
            scan_hits = _scan_by_salt(handle, list(pa_map.keys()))
            for salt_hex, (_addr, key_hex) in scan_hits.items():
                enc_key = bytes.fromhex(key_hex)
                page1 = pa_map[salt_hex][0][2]
                if _verify_key_hmac(page1, enc_key):
                    personal_results[salt_hex] = key_hex
        finally:
            windll.kernel32.CloseHandle(handle)

    all_results = {**global_results, **personal_results}
    if not all_results:
        return RefreshResult(False, "未能提取任何数据库密钥", errors=["no_key_found"])

    # 5. Snapshot and decrypt all databases into one staged refresh set.
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_root = Path(snapshot_root or (output_dir / ".snapshots"))
    snapshot_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="refresh-", dir=snapshot_root))
    input_dir = run_dir / "encrypted"
    staged_dir = run_dir / "decrypted"
    input_dir.mkdir()
    staged_dir.mkdir()
    ok_count = 0
    fail_count = 0
    refreshed_names: set[str] = set()

    decrypt_jobs = []
    for _account, _fp, page1 in global_dbs:
        sh = page1[:SALT_SIZE].hex()
        if sh in global_results:
            decrypt_jobs.append((_fp, bytes.fromhex(global_results[sh])))
    for sh, kh in personal_results.items():
        for _account, fp, _page1 in pa_map[sh]:
            decrypt_jobs.append((fp, bytes.fromhex(kh)))

    available_files = {
        os.path.basename(path).lower() for path, _key in decrypt_jobs
    }
    required_files = _required_refresh_files(available_files)
    missing_core = sorted(required_files)
    try:
        for src_path, enc_key in decrypt_jobs:
            fn = os.path.basename(src_path)
            snapshot_path = input_dir / fn
            dst_path = staged_dir / fn
            try:
                _copy_database_snapshot(src_path, snapshot_path)
                required_tables = {
                    "nt_msg.db": {"group_msg_table"},
                    "group_info.db": {"group_list"},
                    "group_msg_fts.db": {"group_msg_fts"},
                }.get(fn.lower())
                if _decrypt_db(
                    snapshot_path,
                    dst_path,
                    enc_key,
                    required_tables=required_tables,
                    check_integrity=required_tables is not None,
                ):
                    ok_count += 1
                    refreshed_names.add(fn.lower())
                else:
                    fail_count += 1
                    errors.append(f"解密或完整性校验失败: {fn}")
            except Exception as exc:
                fail_count += 1
                errors.append(f"快照或解密异常 {fn}: {exc}")
                logger.warning("Snapshot/decrypt failed for %s: %s", fn, exc)

        missing_core = _publish_staged_outputs(
            staged_dir,
            output_dir,
            refreshed_names=refreshed_names,
            required_files=required_files,
        )
        if missing_core:
            errors.append("核心数据库未刷新: " + ", ".join(missing_core))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    elapsed = time.time() - start
    msg = f"刷新完成: {ok_count} 个数据库成功, {fail_count} 个失败, 耗时 {elapsed:.1f}s"
    return RefreshResult(
        success=not missing_core,
        message=msg,
        files_decrypted=ok_count,
        files_failed=fail_count,
        output_dir=str(output_dir),
        duration_seconds=elapsed,
        errors=errors,
    )
