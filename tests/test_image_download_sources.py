import json
import sqlite3

import pytest

from qq_digest.attachment_sources import attachment_refs
from qq_digest.collector.protobuf import make_bytes_field, make_varint_field
from tests.test_ntqq_collector import ntqq_dbs, ntqq_fts_dbs


def image_blob(*fields, host=b'', kind=2):
    inner = (make_varint_field(45002, kind)
        + make_bytes_field(45402, b'photo.png') + make_bytes_field(45406, b'x' * 16))
    inner += b''.join(make_bytes_field(number, value) for number, value in fields)
    if host:
        inner += make_bytes_field(45816, host)
    return make_bytes_field(40800, inner)


@pytest.mark.parametrize('field', [45802, 45803, 45804])
@pytest.mark.parametrize('prefix', ['', 'https://multimedia.nt.qq.com.cn'])
def test_image_source_retains_original_path_and_strips_credentials(field, prefix):
    url = prefix + '/download?appid=1407&fileid=original_Id-123&spec=0&rkey=private&token=secret'
    ref = attachment_refs(image_blob((field, url.encode()), host=b'multimedia.nt.qq.com.cn'))[0]
    assert ref['download_path'] == '/download?appid=1407&fileid=original_Id-123&spec=0'
    assert 'private' not in json.dumps(ref) and 'secret' not in json.dumps(ref)


def test_original_source_wins_over_thumbnail_without_changing_its_spec():
    ref = attachment_refs(image_blob(
        (45802, b'/download?appid=1407&fileid=thumbnail-1234&spec=198'),
        (45803, b'/download?appid=1406&fileid=original_Id-123&spec=0'),
        (45804, b'/download?appid=1407&fileid=preview-1234&spec=720')))[0]
    assert ref['download_path'] == '/download?appid=1406&fileid=original_Id-123&spec=0'


@pytest.mark.parametrize('url,host', [
    (b'https://elsewhere.example/download?appid=1407&fileid=original_Id-123&spec=0', b''),
    (b'/download?appid=1407&fileid=original_Id-123&spec=0', b'elsewhere.example'),
    (b'/download?appid=1407&fileid=original_Id-123&spec=0', b'multimedia.nt.qq.com.cn:443'),
    (b'http://multimedia.nt.qq.com.cn/download?appid=1407&fileid=original_Id-123&spec=0', b''),
    (b'file:///download?appid=1407&fileid=original_Id-123&spec=0', b''),
    (b'/download?appid=1407&fileid=original_Id-123&spec=198', b''),
    (b'/download?appid=1407&fileid=original_Id-123', b''),
    (b'/download?appid=1407&fileid=original_Id-123&spec=0&spec=198', b''),
    (b'/download?appid=1407&fileid=original_Id-123&spec=0\x00', b''),
    (b'\xff/download?appid=1407&fileid=original_Id-123&spec=0', b''),
])
def test_untrusted_or_non_original_sources_do_not_change_normal_refs(url, host):
    assert attachment_refs(image_blob((45802, url), host=host))[0] == {
        'key': 'image:' + '78' * 16 + ':0', 'kind': 'image',
        'name': 'photo.png', 'path': '', 'md5': '78' * 16}


def test_file_and_text_and_file_id_cannot_become_image_download_sources():
    url = b'/download?appid=1407&fileid=original_Id-123&spec=0'
    assert 'download_path' not in attachment_refs(image_blob((45802, url), kind=3))[0]
    assert 'download_path' not in attachment_refs(image_blob((45101, url), (45503, url)))[0]


def test_media_snapshot_persists_only_safe_download_path(ntqq_fts_dbs, tmp_path):
    from tests.test_message_media_index import build, current_database
    source = current_database(tmp_path / 'current.db', damaged=False)
    blob = image_blob((45802, b'/download?appid=1407&fileid=original_Id-123&spec=0&rkey=private'))
    with sqlite3.connect(source) as connection:
        connection.execute('UPDATE group_msg_table SET "40800"=? WHERE "40001"=9902', (blob,))
    destination = tmp_path / 'media.db'
    build(source, ntqq_fts_dbs / 'group_msg_fts.db', destination)
    with sqlite3.connect(destination) as connection:
        metadata = connection.execute('SELECT content_json FROM message_media WHERE msg_id="9902"').fetchone()[0]
    assert json.loads(metadata)['attachments'][0]['download_path'] == '/download?appid=1407&fileid=original_Id-123&spec=0'
    assert 'private' not in metadata and 'rkey' not in metadata
