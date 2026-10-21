import hashlib
import io
from datetime import datetime, timezone

from PIL import Image

from tests.test_web import web_client
from qq_digest.models import NormalizedMessage
from qq_digest.secondhand import SecondhandService


def seed_image(client, root, *, forward=False):
    stream=io.BytesIO();Image.new('RGB',(40,30),'red').save(stream,format='PNG')
    data=stream.getvalue();path=root.parent/'original.png';path.write_bytes(data)
    ref={'key':'pic','kind':'image','name':'商品.png','path':str(path),'md5':hashlib.md5(data).hexdigest()}
    now=datetime.now(timezone.utc)
    content={'attachments':[ref]}
    if forward:content={'forward_messages':[{'sender_qq':456,'text':'[图片]','timestamp':now.isoformat(),'attachments':[ref]}]}
    client.app.state.archive.ingest([NormalizedMessage(group_id=123,msg_id='image',sender_qq=321,
        timestamp=now,collected_at=now,message_type='forward' if forward else 'file',text='[图片]',content_json=content)])
    return data,path


def test_original_is_inline_without_starting_ai_and_is_authenticated(web_client,monkeypatch):
    client,_,root=web_client;data,path=seed_image(client,root)
    monkeypatch.setattr('qq_digest.attachments.AttachmentService._vision',lambda *a:(_ for _ in ()).throw(AssertionError('GET must not call AI')))
    url='/api/groups/123/messages/image/images'
    assert client.get(url).status_code==401
    client.post('/login',data={'password':'password123'})
    context=client.get('/api/search/messages/123/image/context').json()
    image=context['messages'][0]['images'][0]
    assert image['status']=='disabled' and image['url']
    assert str(path) not in str(context)
    response=client.get(image['url'])
    assert response.content==data and response.headers['content-type']=='image/png'
    assert response.headers['cache-control']=='no-store'
    path.write_bytes(b'changed')
    assert client.get(image['url']).status_code==404
    assert client.get('/api/groups/999/messages/image/images').status_code==404


def test_read_only_selected_image_and_reuses_cache(web_client,monkeypatch):
    client,_,root=web_client;seed_image(client,root)
    client.post('/login',data={'password':'password123'})
    url='/api/groups/123/messages/image/images/pic/read'
    assert client.post(url,json={}).status_code==409
    client.patch('/api/groups/123',json={'read_images':True})
    calls=[]
    monkeypatch.setattr('qq_digest.attachments.AttachmentService._vision',lambda self,images,name:(calls.append(name) or '显示器 800元',False))
    for _ in range(2):
        response=client.post(url,json={})
        assert response.status_code==200
        assert response.json()['items'][0]['text']=='显示器 800元'
    assert calls==['商品.png']
    ar=client.app.state.archive
    assert ar.connection.execute("SELECT text FROM messages WHERE msg_id='image'").fetchone()[0]=='[图片]'
    with client.app.state.operations.claim('backup'):
        assert client.post(url,json={}).status_code==409
    assert client.post(url.replace('/pic/','/unknown/'),json={}).status_code==404


def test_forward_images_keep_namespaced_keys_and_block_policy(web_client):
    client,_,root=web_client;seed_image(client,root,forward=True)
    client.post('/login',data={'password':'password123'})
    service=SecondhandService(client.app.state.archive);service.add_groups([123])
    url='/api/groups/123/messages/image/images'
    image=client.get(url).json()['items'][0]
    assert len(image['key'])==64 and client.get(image['url']).status_code==200
    service.block(456)
    assert client.get(url).json()['items']==[]
    assert client.get(image['url']).status_code==404
    service.block(321)
    assert client.get(url).status_code==404
