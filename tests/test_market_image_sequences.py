from datetime import timedelta

import pytest

from tests.test_web import web_client
from tests.test_secondhand import message, offer, ingest
from tests.test_market_image_cards import seed_market_photo
from qq_digest.secondhand import SecondhandService


def test_duplicate_text_message_keeps_verified_sequence_and_existing_attachments(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    seed_market_photo(client, tmp_path)
    old = archive.connection.execute("SELECT * FROM messages WHERE msg_id='photo-post'").fetchone()
    original = archive._row_to_message(old)
    incoming = original.model_copy(update={'text': 'different text', 'content_json': {'message_sequence': 40, 'attachment_source_unavailable': True}})
    archive.ingest([incoming])
    saved = archive._row_to_message(archive.connection.execute("SELECT * FROM messages WHERE msg_id='photo-post'").fetchone())
    assert saved.text == original.text
    assert saved.content_json['message_sequence'] == 40
    assert saved.content_json['attachment_source_unavailable'] is True
    assert saved.content_json['attachments'] == original.content_json['attachments']
    archive.ingest([incoming.model_copy(update={'timestamp': incoming.timestamp + timedelta(seconds=1), 'content_json': {'message_sequence': 99}})])
    saved = archive._row_to_message(archive.connection.execute("SELECT * FROM messages WHERE msg_id='photo-post'").fetchone())
    assert saved.content_json['message_sequence'] == 40


def seed_continuous(client, root, *, gap=False, other_sender=False, after=False, competing=False, forwarded=False, unknown_sequence=False):
    service, _, _, _ = seed_market_photo(client, root)
    archive = client.app.state.archive
    original = archive._row_to_message(archive.connection.execute("SELECT * FROM messages WHERE msg_id='photo-post'").fetchone())
    ref = original.content_json['attachments'][0]
    archive.connection.execute("DELETE FROM market_posts")
    archive.connection.execute("DELETE FROM messages WHERE msg_id='photo-post'")
    archive.connection.commit()
    anchor = message(gid=123, mid='anchor', text='出售显示器800元', metadata={'message_sequence': 10, 'sender_uid': 'seller'})
    if forwarded:
        anchor = anchor.model_copy(update={'message_type': 'forward', 'content_json': {'message_sequence': 10, 'sender_uid': 'seller', 'forward_messages': [{'sender_qq': 100, 'timestamp': anchor.timestamp.isoformat(), 'text': anchor.text}]}})
    seq = 11 if after else 8 if gap else 9
    photo = message(gid=123, mid='separate-photo', text='[图片]', kind='file', sender=200 if other_sender else 100,
                    metadata={'attachments': [ref], 'sender_uid': 'other' if other_sender else 'seller', **({} if unknown_sequence else {'message_sequence': seq})})
    photo = photo.model_copy(update={'timestamp': anchor.timestamp + timedelta(seconds=11 if after else -11)})
    messages = [photo, anchor]
    if competing:
        messages.append(message(gid=123, mid='another-product', text='出售键盘80元', metadata={'message_sequence': 8, 'sender_uid': 'seller'}).model_copy(update={'timestamp': anchor.timestamp - timedelta(seconds=22)}))
    units = ingest(service, messages)
    source = next(u for u in units if u['msg_id'] == 'anchor')
    service.accept_units([source], {'items': [offer(source, title='显示器')]})
    item = service.list_items(date_from='2026-10-13', date_to='2026-10-19')['items'][0]
    return service, item['item_id'], photo


@pytest.mark.parametrize('after', [False, True])
def test_continuous_same_seller_photos_have_original_source_provenance(web_client, tmp_path, after):
    client, _, _ = web_client
    _, item_id, photo = seed_continuous(client, tmp_path, after=after)
    client.post('/login', data={'password': 'password123'})
    for path in ['/api/secondhand/items/' + item_id, '/api/secondhand?date_from=2026-10-13&date_to=2026-10-19&grouped=true']:
        result = client.get(path).json()
        item = result['item'] if 'item' in result else result['items'][0]
        image = item['images'][0]
        assert image['related'] is True
        assert image['source_msg_id'] == photo.msg_id
        assert image['source_timestamp'] == photo.timestamp.isoformat()
        assert 'msg_id=separate-photo' in image['url']
        assert client.get(image['url']).status_code == 200
        assert client.get(image['source_url']).json()['anchor_id'] == photo.msg_id


@pytest.mark.parametrize('kwargs', [{'gap': True}, {'other_sender': True}, {'competing': True}, {'forwarded': True}, {'unknown_sequence': True}])
def test_continuous_photos_do_not_cross_unknown_or_ambiguous_sources(web_client, tmp_path, kwargs):
    client, _, _ = web_client
    _, item_id, _ = seed_continuous(client, tmp_path, **kwargs)
    client.post('/login', data={'password': 'password123'})
    assert client.get('/api/secondhand/items/' + item_id).json()['item']['images'] == []


def test_continuous_photo_range_is_bounded_and_blocked_source_stays_hidden(web_client, tmp_path):
    client, _, _ = web_client
    service, item_id, photo = seed_continuous(client, tmp_path)
    client.post('/login', data={'password': 'password123'})
    archive = service.archive
    archive.connection.execute("UPDATE messages SET timestamp=? WHERE msg_id='separate-photo'", ((photo.timestamp - timedelta(minutes=5)).isoformat(),))
    archive.connection.commit()
    assert client.get('/api/secondhand/items/' + item_id).json()['item']['images'] == []
    service.block(100)
    assert client.get('/api/secondhand/items/' + item_id).status_code == 404
