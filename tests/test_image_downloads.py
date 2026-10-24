import hashlib
from http.client import HTTPResponse
import io
import stat
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from qq_digest import image_downloads as downloads


DOWNLOAD = '/download?appid=1407&fileid=abcdefghijk_123-XYZ&spec=0'


def png(size=(4, 5)):
    stream = io.BytesIO()
    Image.new('RGB', size, 'red').save(stream, format='PNG')
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, *, mime='image/png', status=200, url=None, length=None):
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Type': mime, 'Content-Length': str(len(data) if length is None else length)}
        self.url = url

    def geturl(self):
        return self.url


@pytest.fixture
def setup_download(monkeypatch, tmp_path):
    monkeypatch.setattr(downloads.runtime_paths, 'cache_root', lambda: tmp_path / 'cache')
    monkeypatch.setattr(downloads, '_keys', [])
    monkeypatch.setattr(downloads, '_keys_until', 0.0)
    monkeypatch.setattr(downloads, '_failures', {})
    data = png()
    ref = {'kind': 'image', 'md5': hashlib.md5(data).hexdigest(), 'download_path': DOWNLOAD}
    config = SimpleNamespace(ntqq=SimpleNamespace(qq_number=42))
    return ref, config, data, tmp_path / 'cache' / 'message-images' / '42' / (ref['md5'] + '.img')


def test_canonical_download_path_discards_credentials_and_unknown_fields():
    assert downloads.validate_download_path('https://multimedia.nt.qq.com.cn' + DOWNLOAD + '&rkey=secret&foo=bar') == DOWNLOAD
    assert downloads.validate_download_path(DOWNLOAD.replace('1407', '1406')) == DOWNLOAD.replace('1407', '1406')


@pytest.mark.parametrize('value', [
    DOWNLOAD.replace('spec=0', 'spec=198'), DOWNLOAD + '&spec=0',
    'https://evil.test' + DOWNLOAD, 'http://multimedia.nt.qq.com.cn' + DOWNLOAD,
    'https://multimedia.nt.qq.com.cn:443' + DOWNLOAD,
    'https://user@multimedia.nt.qq.com.cn' + DOWNLOAD,
    '//multimedia.nt.qq.com.cn' + DOWNLOAD, DOWNLOAD + '#fragment',
    DOWNLOAD.replace('abcdefghijk_123-XYZ', 'short'), DOWNLOAD + '&rkey=%0aattack',
    DOWNLOAD.replace('/download?', '/other?'), DOWNLOAD.replace('1407', '9999'),
    DOWNLOAD.replace('fileid=', 'fileid=%2f'), DOWNLOAD + '&rkey=one&rkey=two',
])
def test_download_path_rejects_unsafe_or_non_original_sources(value):
    with pytest.raises(ValueError):
        downloads.validate_download_path(value)


def test_verified_cache_is_available_without_qq_or_download_url(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    ref.pop('download_path')
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: pytest.fail('must not scan QQ'))
    assert downloads.cached_image_path(ref, config) == target
    assert downloads.download_original(ref, config) == target


def test_download_tries_candidates_and_prefers_working_key_for_next_image(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    scans = []
    calls = []
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **kw: scans.append(kw) or ['first', 'second', 'third'])
    def open_url(request, timeout):
        calls.append(parse_qs(urlsplit(request.full_url).query)['rkey'][0])
        assert timeout == 6
        if calls[-1] != 'third':
            raise HTTPError(request.full_url, 400, 'Bad request', {}, None)
        return Response(data)
    monkeypatch.setattr(downloads, '_open', open_url)
    assert downloads.download_original(ref, config).read_bytes() == data
    assert calls == ['first', 'second', 'third']
    target.unlink()
    assert downloads.download_original(ref, config) == target
    assert calls[-1] == 'third' and len(calls) == 4 and len(scans) == 1


@pytest.mark.parametrize('change', [{'kind': 'file'}, {'md5': '../bad'}, {'download_path': 'https://evil.test/a'}])
def test_bad_metadata_never_scans_or_uses_network(setup_download, monkeypatch, change):
    ref, config, _, _ = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: pytest.fail('must not scan'))
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: pytest.fail('must not fetch'))
    with pytest.raises(ValueError):
        downloads.download_original({**ref, **change}, config)


@pytest.mark.parametrize('payload,mime,length', [(b'<html>bad</html>', 'text/html', None), (b'garbage', 'image/png', None), (b'X' * 32, 'image/png', 17 * 1024 * 1024)])
def test_bad_payload_never_leaves_cache_file(setup_download, monkeypatch, payload, mime, length):
    ref, config, _, target = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: Response(payload, mime=mime, length=length))
    with pytest.raises(ValueError):
        downloads.download_original(ref, config)
    assert not target.exists()
    assert not list(target.parent.glob('*.tmp'))


@pytest.mark.parametrize('size', [(8193, 1), (5000, 4001)])
def test_pixel_limits_reject_image_even_when_digest_matches(setup_download, monkeypatch, size):
    ref, config, _, target = setup_download
    data = png(size)
    ref['md5'] = hashlib.md5(data).hexdigest()
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: Response(data))
    with pytest.raises(ValueError):
        downloads.download_original(ref, config)
    assert not list(target.parent.glob('*.img'))


def test_hash_mismatch_and_corrupt_cached_data_are_never_reused(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    target.parent.mkdir(parents=True)
    target.write_bytes(png((2, 3)))
    assert downloads.cached_image_path(ref, config) is None
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: Response(png((1, 1))))
    with pytest.raises(ValueError):
        downloads.download_original(ref, config)
    assert target.read_bytes() != data


def test_redirect_is_rejected_and_exception_hides_url_and_key(setup_download, monkeypatch):
    ref, config, _, target = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['private-secret'])
    monkeypatch.setattr(downloads, '_open', lambda req, **_: (_ for _ in ()).throw(HTTPError(req.full_url, 302, 'redirect private-secret', {'Location': 'https://evil.test'}, None)))
    with pytest.raises(ValueError) as error:
        downloads.download_original(ref, config)
    assert 'private-secret' not in str(error.value) and 'http' not in str(error.value)
    assert error.value.__cause__ is None
    assert not target.exists()


def test_failure_cooldown_prevents_scans_and_requests_until_retry(setup_download, monkeypatch):
    ref, config, _, _ = setup_download
    clock = [1000.0]
    scans, calls = [], []
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: scans.append(1) or ['key'])
    def fail(req, **_):
        calls.append(1)
        raise OSError(req.full_url)
    monkeypatch.setattr(downloads, '_open', fail)
    for _ in range(2):
        with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert len(scans) == len(calls) == 1
    clock[0] += 61
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert len(scans) == 1 and len(calls) == 2


def test_concurrent_identical_downloads_write_once(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    calls = []
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    def fetch(*_a, **_k):
        calls.append(1)
        return Response(data)
    monkeypatch.setattr(downloads, '_open', fetch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: downloads.download_original(ref, config), range(4)))
    assert paths == [target] * 4 and calls == [1]


def test_cache_symlink_is_never_read_or_written(setup_download, monkeypatch, tmp_path):
    ref, config, data, target = setup_download
    outside = tmp_path / 'outside'
    outside.mkdir()
    target.parent.parent.parent.mkdir(parents=True)
    try:
        target.parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: pytest.fail('must not scan'))
    assert downloads.cached_image_path(ref, config) is None
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert not list(outside.iterdir())


def test_total_budget_includes_scan_and_stops_remaining_candidate_requests(setup_download, monkeypatch):
    ref, config, _, target = setup_download
    clock = [1000.0]
    calls, timeouts = [], []
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: clock[0])
    def scan(**_):
        clock[0] += 9
        return ['first', 'second', 'third', 'fourth']
    monkeypatch.setattr(downloads, 'scan_image_keys', scan)
    def denied(request, timeout):
        calls.append(1)
        timeouts.append(timeout)
        clock[0] += timeout
        raise HTTPError(request.full_url, 400, 'denied', {}, None)
    monkeypatch.setattr(downloads, '_open', denied)
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert len(calls) == 2 and timeouts == [6, 5]
    assert not target.exists()


def test_slow_stream_stops_at_absolute_deadline(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    clock = [1000.0]
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    class SlowResponse(Response):
        def read1(self, amount):
            clock[0] += 6
            return b'x'
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: SlowResponse(data))
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert clock[0] == 1024
    assert not target.exists()


def test_read_limit_is_enforced_without_content_length(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    class UnboundedResponse(Response):
        def __init__(self):
            super().__init__(data)
            self.headers.pop('Content-Length')
            self.received = 0
        def read1(self, amount):
            assert 0 < amount <= 65536
            self.received += amount
            return b'x' * amount
    response = UnboundedResponse()
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: response)
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert response.received == 16 * 1024 * 1024 + 1
    assert not target.exists()


def test_mismatched_declared_mime_is_rejected(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: Response(data, mime='image/jpeg'))
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert not target.exists()


def test_credential_attempts_are_capped_and_scan_is_reused_until_ttl(setup_download, monkeypatch):
    ref, config, _, _ = setup_download
    clock, scans, requests = [1000.0], [], []
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: scans.append(1) or [f'key{i}' for i in range(30)])
    def denied(request, **_):
        requests.append(1)
        raise HTTPError(request.full_url, 401, 'denied', {}, None)
    monkeypatch.setattr(downloads, '_open', denied)
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert len(requests) == 16
    clock[0] += 61
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert len(requests) == 32 and len(scans) == 1
    clock[0] += 600
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert len(scans) == 2


def test_empty_scanner_has_shared_cooldown_across_different_images(setup_download, monkeypatch):
    ref, config, _, _ = setup_download
    scans = []
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: scans.append(1) or [])
    for digest in ['1' * 32, '2' * 32]:
        with pytest.raises(ValueError): downloads.download_original({**ref, 'md5': digest}, config)
    assert scans == [1]


def test_failure_memory_is_bounded(setup_download, monkeypatch):
    ref, config, _, _ = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: [])
    for index in range(257):
        with pytest.raises(ValueError): downloads.download_original({**ref, 'md5': f'{index:032x}'}, config)
    assert len(downloads._failures) == 256


def test_reparse_ancestor_is_rejected_before_scan_or_mkdir(setup_download, monkeypatch):
    ref, config, _, target = setup_download
    original = type(target).lstat
    def marked(path, *args, **kwargs):
        if path == target.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(target), 'lstat', marked)
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: pytest.fail('must not scan'))
    assert downloads.cached_image_path(ref, config) is None
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert not target.parent.exists()


def test_redirect_handler_never_creates_followup_request():
    from urllib.request import Request
    request = Request('https://multimedia.nt.qq.com.cn' + DOWNLOAD + '&rkey=private')
    assert downloads._NoRedirect().redirect_request(request, None, 302, 'moved', {}, 'https://evil.test') is None


def test_failed_atomic_replace_leaves_no_partial_file(setup_download, monkeypatch):
    ref, config, data, target = setup_download
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    monkeypatch.setattr(downloads, '_open', lambda *_a, **_k: Response(data))
    monkeypatch.setattr(downloads.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('private-file-content')))
    with pytest.raises(ValueError) as error: downloads.download_original(ref, config)
    assert 'private-file-content' not in str(error.value)
    assert not target.exists() and not list(target.parent.iterdir())


def test_stdlib_response_trickle_checks_deadline_between_socket_receives(setup_download, monkeypatch):
    ref, config, _, target = setup_download
    clock = [1000.0]
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(downloads, 'scan_image_keys', lambda **_: ['key'])
    class SlowRaw(io.RawIOBase):
        def __init__(self):
            self.header = b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 100\r\n\r\n'
            self.body_reads = 0
            self.timeout = 6
            self._sock = self
        def readable(self):
            return True
        def settimeout(self, timeout):
            self.timeout = timeout
        def readinto(self, buffer):
            if self.header:
                count = min(len(buffer), len(self.header))
                buffer[:count], self.header = self.header[:count], self.header[count:]
                return count
            self.body_reads += 1
            clock[0] += min(6, self.timeout)
            buffer[0] = 120
            return 1
    raw = SlowRaw()
    class Socket:
        def makefile(self, mode):
            return io.BufferedReader(raw)
    def fetch(request, timeout):
        response = HTTPResponse(Socket(), method='GET', url=request.full_url)
        response.url = request.full_url
        response.begin()
        return response
    monkeypatch.setattr(downloads, '_open', fetch)
    with pytest.raises(ValueError): downloads.download_original(ref, config)
    assert clock[0] == 1020 and raw.body_reads == 4
    assert not target.exists()
