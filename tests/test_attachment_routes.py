from datetime import datetime,timezone
import hashlib

from tests.test_web import web_client
from qq_digest.models import NormalizedMessage


def test_group_read_switches_validation_scope_and_routes(web_client):
    client,_,root=web_client
    assert client.get('/api/groups/123/attachments').status_code==401
    assert client.post('/api/groups/123/attachments/read',json={}).status_code==401
    client.post('/login',data={'password':'password123'})
    assert client.patch('/api/groups/123',json={'read_files':'false'}).status_code==422
    assert client.patch('/api/groups/123',json={'read_files':True,'read_images':False,'expected_revision':0}).status_code==200
    ar=client.app.state.archive
    assert next(g for g in ar.all_groups() if g.group_id==123).read_files
    path=root.parent/'合成.txt';data='附件关键词和内容'.encode();path.write_bytes(data)
    msg=NormalizedMessage(group_id=123,msg_id='attachment-1',timestamp=datetime.now(timezone.utc),collected_at=datetime.now(timezone.utc),message_type='file',text='[文件] 合成.txt',
        content_json={'attachments':[{'key':'f1','kind':'file','name':'合成.txt','path':str(path),'md5':hashlib.md5(data).hexdigest()}]})
    ar.ingest([msg])
    result=client.get('/api/groups/123/attachments').json()
    assert result['items'][0]['status']=='pending' and 'path' not in str(result['items'])
    assert client.post('/api/groups/123/attachments/read',json={}).status_code==200
    result=client.get('/api/groups/123/attachments').json()
    assert result['items'][0]['status']=='success' and '关键词' in result['items'][0]['text']
    client.patch('/api/groups/123',json={'read_files':False})
    assert client.post('/api/groups/123/attachments/read',json={}).status_code==409
    assert client.get('/api/groups/999/attachments').status_code==404
    page=client.get('/groups').text
    assert '读取图片' in page and '读取文件' in page and 'Cockpit' in page


def test_reading_and_policy_changes_participate_in_operation_lock(web_client):
    client,_,_=web_client;client.post('/login',data={'password':'password123'})
    with client.app.state.operations.claim('backup'):
        assert client.patch('/api/groups/123',json={'read_images':True}).status_code==409
        assert client.post('/api/groups/123/attachments/read',json={}).status_code==409
