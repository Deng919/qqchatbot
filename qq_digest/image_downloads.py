"""Bounded original-image retrieval from QQ's fixed media CDN.

Credentials exist only in process memory. Callers enforce group authorization.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import warnings

from PIL import Image

from . import runtime_paths
from .storage_files import safe_path

MAX_BYTES = 16 * 1024 * 1024
_HOST = 'multimedia.nt.qq.com.cn'
_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp'}
_lock = threading.RLock()
_keys: list[str] = []
_keys_until = 0.0
_failures: dict[tuple[int, str], float] = {}


class ImageDownloadError(ValueError):
    """A deliberately credential-free image download error."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_open = build_opener(_NoRedirect()).open


def scan_image_keys(*, limit=16, seconds=10):
    # Lazy import keeps already verified images usable with QQ closed.
    from .qq_image_auth import scan_image_keys as scan
    return scan(limit=limit, seconds=seconds)


def validate_download_path(value: str) -> str:
    """Accept only original Tencent media links, returning credential-free metadata."""
    invalid = ImageDownloadError('图片来源地址不可用')
    if not isinstance(value, str) or len(value) > 2048 or not value or re.search(r'[^\x21-\x7e]', value):
        raise invalid
    try:
        url = urlsplit(value)
    except ValueError:
        raise invalid from None
    if url.fragment or '#' in value or '\\' in value:
        raise invalid
    if url.scheme or url.netloc:
        if url.scheme != 'https' or url.netloc != _HOST:
            raise invalid
    elif not value.startswith('/download?'):
        raise invalid
    if url.path != '/download' or not url.query:
        raise invalid
    query = {}
    for item in url.query.split('&'):
        if item.count('=') != 1:
            raise invalid
        name, content = item.split('=', 1)
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', name) or
                not re.fullmatch(r'[A-Za-z0-9._~-]{0,1024}', content) or name in query):
            raise invalid
        query[name] = content
    if (query.get('appid') not in ('1406', '1407') or query.get('spec') != '0' or
            not re.fullmatch(r'[A-Za-z0-9_-]{10,512}', query.get('fileid', ''))):
        raise invalid
    return '/download?' + urlencode({name: query[name] for name in ('appid', 'fileid', 'spec')})


def _target(ref, config):
    if not isinstance(ref, dict) or ref.get('kind') != 'image':
        raise ImageDownloadError('不是图片附件')
    digest = str(ref.get('md5') or '').lower()
    account = getattr(getattr(config, 'ntqq', None), 'qq_number', None)
    if not re.fullmatch(r'[0-9a-f]{32}', digest) or type(account) is not int or account <= 0:
        raise ImageDownloadError('无法核实图片来源')
    try:
        root = safe_path(runtime_paths.cache_root())
        target = safe_path(root / 'message-images' / str(account) / (digest + '.img'), root)
    except Exception:
        raise ImageDownloadError('图片缓存路径不可用') from None
    return target, digest, account


def _verify_image(data, digest, mime=None):
    if len(data) > MAX_BYTES:
        raise ImageDownloadError('图片超过 16 MB')
    if hashlib.md5(data).hexdigest() != digest:
        raise ImageDownloadError('附件与原消息不匹配')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                expected = _TYPES.get(image.format)
                if not expected or (mime is not None and mime != expected):
                    raise ImageDownloadError('图片格式不可用')
                if min(image.size) < 1 or max(image.size) > 8192 or image.width * image.height > 20_000_000:
                    raise ImageDownloadError('图片尺寸超限')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except ImageDownloadError:
        raise
    except Exception:
        raise ImageDownloadError('图片数据不可用') from None


def _cached(target, digest):
    try:
        safe_path(target)
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return None
        with target.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        _verify_image(data, digest)
        return target
    except Exception:
        return None


def cached_image_path(ref, config) -> Path | None:
    """Return only verified original bytes inside this account's managed cache."""
    try:
        target, digest, _ = _target(ref, config)
        return _cached(target, digest)
    except Exception:
        return None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ImageDownloadError('图片下载超时，请稍后重试')
    return remaining


def _credentials(now, deadline):
    global _keys, _keys_until
    if now >= _keys_until:
        _keys = []
        _keys_until = now + 60
        try:
            candidates = scan_image_keys(limit=16, seconds=min(10, _remaining(deadline)))
            # Discard malformed candidates before placing any in a URL.
            for key in candidates[:16]:
                if isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_=-]{1,2048}', key) and key not in _keys:
                    _keys.append(key)
        except Exception:
            pass
        if _keys:
            _keys_until = now + 600
    return list(_keys[:16])


def _fetch(path, key, digest, deadline):
    url = 'https://' + _HOST + path + '&' + urlencode({'rkey': key})
    request = Request(url, headers={'Accept': 'image/jpeg,image/png,image/gif,image/webp', 'User-Agent': 'QQDigestDesktop'})
    try:
        with _open(request, timeout=min(6, _remaining(deadline))) as response:
            if response.status != 200:
                raise ImageDownloadError('图片下载失败')
            final = response.geturl()
            if final and final != url:
                raise ImageDownloadError('图片下载重定向已拒绝')
            mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if mime not in _TYPES.values():
                raise ImageDownloadError('图片格式不可用')
            length = response.headers.get('Content-Length')
            if length is not None and (not re.fullmatch(r'\d{1,12}', length) or int(length) > MAX_BYTES):
                raise ImageDownloadError('图片超过 16 MB')
            data = bytearray()
            while True:
                timeout = min(6, _remaining(deadline))
                # urllib's response keeps the TLS socket here. Shrink its read
                # timeout as the shared scan/request budget is consumed.
                socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if socket is not None:
                    socket.settimeout(timeout)
                # read() may refill repeatedly while a peer trickles bytes;
                # read1() returns after one buffered/raw receive, so the next
                # iteration can enforce the absolute deadline.
                chunk = response.read1(min(64 * 1024, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ImageDownloadError('图片超过 16 MB')
            _remaining(deadline)
            _verify_image(data, digest, mime)
            return data
    except HTTPError as error:
        if error.code in (400, 401, 403):
            return None
        raise ImageDownloadError('图片下载失败') from None
    except ImageDownloadError:
        raise
    except Exception:
        raise ImageDownloadError('图片下载失败，请稍后重试') from None


def _store(target, data):
    temporary = target.parent / ('.download-' + secrets.token_hex(16) + '.tmp')
    try:
        safe_path(target)
        safe_path(temporary, target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_path(target)
        safe_path(temporary, target.parent)
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(target)
        safe_path(temporary, target.parent)
        os.replace(temporary, target)
    except Exception:
        raise ImageDownloadError('图片缓存写入失败') from None
    finally:
        try:
            safe_path(temporary, target.parent).unlink(missing_ok=True)
        except Exception:
            pass


def download_original(ref, config) -> Path:
    """Fetch a verified original once; bound credentials, retries, memory and disk."""
    deadline = time.monotonic() + 20
    target, digest, account = _target(ref, config)
    if not _lock.acquire(timeout=_remaining(deadline)):
        raise ImageDownloadError('图片下载繁忙，请稍后重试')
    try:
        cached = _cached(target, digest)
        if cached is not None:
            return cached
        path = validate_download_path(ref.get('download_path'))
        now = time.monotonic()
        identity = (account, digest)
        if _failures.get(identity, 0) > now:
            raise ImageDownloadError('图片暂不可用，请稍后重试')
        try:
            for key in _credentials(now, deadline):
                data = _fetch(path, key, digest, deadline)
                if data is None:
                    continue
                _store(target, data)
                _keys.remove(key)
                _keys.insert(0, key)
                _failures.pop(identity, None)
                return target
            raise ImageDownloadError('图片下载凭据不可用，请保持 QQ 登录后稍后重试')
        except Exception as error:
            _failures[identity] = time.monotonic() + 60
            while len(_failures) > 256:
                _failures.pop(next(iter(_failures)))
            message = str(error) if isinstance(error, ImageDownloadError) else '图片下载失败，请稍后重试'
            raise ImageDownloadError(message) from None
    finally:
        _lock.release()
