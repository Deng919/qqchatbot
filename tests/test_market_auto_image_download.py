import hashlib
import io

import pytest
from PIL import Image

from qq_digest.attachments import AttachmentService
from tests.test_web import web_client
from tests.test_market_image_cards import seed_market_photo


def test_only_marketplace_image_routes_enable_automatic_download(web_client, tmp_path, monkeypatch):
    client, _, _ = web_client
    service, item_id, data, _ = seed_market_photo(client, tmp_path)
    calls = []
    def original(self, ref, *, download=False):
        calls.append(('original', download))
        return data, 'image/png'
    def preview(self, ref, *, download=False):
        calls.append(('preview', download))
        return data, 'image/png'
    monkeypatch.setattr('qq_digest.attachments.AttachmentService.original_image', original)
    monkeypatch.setattr('qq_digest.attachments.AttachmentService.preview_image', preview)
    monkeypatch.setattr('qq_digest.attachments.AttachmentService._vision', lambda *args: (_ for _ in ()).throw(AssertionError('display must not call AI')))
    client.post('/login', data={'password': 'password123'})
    image = client.get('/api/secondhand/items/' + item_id).json()['item']['images'][0]
    assert client.get(image['url']).status_code == 200
    assert calls[-1] == ('original', True)
    assert client.get(image['url'] + '&preview=true').status_code == 200
    assert calls[-1] == ('preview', True)
    generic = '/api/groups/123/messages/photo-post/images/original?key=photo-0'
    assert client.get(generic).status_code == 200
    assert calls[-1] == ('original', True)
    assert client.get(generic + '&preview=true').status_code == 200
    assert calls[-1] == ('preview', True)
    service.db.execute('DELETE FROM market_groups WHERE group_id=123')
    service.db.commit()
    assert client.get(generic).status_code == 200
    assert calls[-1] == ('original', False)
    assert client.get(generic + '&preview=true').status_code == 404
    assert client.app.state.archive.all_groups()[0].read_images is False


def test_auto_download_cannot_bypass_login_group_or_source_blocks(web_client, tmp_path, monkeypatch):
    client, _, _ = web_client
    service, item_id, _, _ = seed_market_photo(client, tmp_path)
    calls = []
    def original(self, ref, *, download=False):
        calls.append(download)
        return b'not reached', 'image/png'
    monkeypatch.setattr('qq_digest.attachments.AttachmentService.original_image', original)
    client.post('/login', data={'password': 'password123'})
    image = client.get('/api/secondhand/items/' + item_id).json()['item']['images'][0]
    generic = '/api/groups/123/messages/photo-post/images/original?key=photo-0'
    client.cookies.clear()
    assert client.get(image['url']).status_code == 401
    assert client.get(generic).status_code == 401
    client.post('/login', data={'password': 'password123'})
    service.db.execute('UPDATE groups SET enabled=0 WHERE group_id=123')
    service.db.commit()
    assert client.get(image['url']).status_code == 403
    assert client.get(generic).status_code == 403
    service.db.execute('UPDATE groups SET enabled=1 WHERE group_id=123')
    service.db.commit()
    service.block(100)
    assert client.get(image['url']).status_code == 404
    assert client.get(generic).status_code == 404
    assert calls == []


@pytest.mark.parametrize('preview_first', [False, True])
def test_missing_original_downloads_once_then_preview_and_original_reuse_cache(web_client, tmp_path, monkeypatch, preview_first):
    client, _, _ = web_client
    _, _, data, source = seed_market_photo(client, tmp_path)
    ref = {'kind': 'image', 'key': 'photo-0', 'md5': hashlib.md5(data).hexdigest(), 'path': str(source), 'name': 'photo.png',
           'download_path': '/download?appid=1407&fileid=synthetic-image-file&spec=0'}
    source.unlink()
    cached = tmp_path / 'verified-download.img'
    calls = []
    def download(ref, config):
        calls.append(ref['md5'])
        cached.write_bytes(data)
        return cached
    monkeypatch.setattr('qq_digest.image_downloads.download_original', download)
    monkeypatch.setattr('qq_digest.image_downloads.cached_image_path', lambda ref, config: cached if cached.exists() else None)
    monkeypatch.setattr('qq_digest.attachments.AttachmentService._vision', lambda *args: (_ for _ in ()).throw(AssertionError('no AI')))
    reader = AttachmentService(client.app.state.archive, client.app.state.config)
    if preview_first:
        preview, mime = reader.preview_image(ref, download=True)
        assert mime == 'image/jpeg'
        with Image.open(io.BytesIO(preview)) as decoded: assert decoded.size == (40, 30)
    else:
        assert reader.original_image(ref, download=True) == (data, 'image/png')
    assert reader.original_image(ref) == (data, 'image/png')
    assert reader.preview_image(ref)[1] == 'image/jpeg'
    assert len(calls) == 1


@pytest.mark.parametrize('method', ['original_image', 'preview_image'])
def test_download_fallback_still_checks_the_original_digest(web_client, tmp_path, monkeypatch, method):
    client, _, _ = web_client
    _, _, data, source = seed_market_photo(client, tmp_path)
    source.unlink()
    wrong = tmp_path / 'wrong.img'
    Image.new('RGB', (40, 30), 'red').save(wrong, format='PNG')
    monkeypatch.setattr('qq_digest.image_downloads.download_original', lambda ref, config: wrong)
    monkeypatch.setattr('qq_digest.image_downloads.cached_image_path', lambda ref, config: None)
    reader = AttachmentService(client.app.state.archive, client.app.state.config)
    with pytest.raises(ValueError, match='附件与原消息不匹配'):
        getattr(reader, method)({'kind': 'image', 'md5': hashlib.md5(data).hexdigest(), 'path': str(source)}, download=True)
