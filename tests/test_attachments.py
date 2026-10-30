import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.attachments import AttachmentService, cached_messages


def setup_message(tmp_path, *, group_id=11, kind='file', name='说明.txt', content=b'hello attachment', enabled=True):
    ar=Archive.open(tmp_path/'a.sqlite')
    ar.upsert_groups([GroupConfig(group_id=group_id,name='合成群',read_images=enabled,read_files=enabled)])
    path=tmp_path/name;path.write_bytes(content)
    msg=NormalizedMessage(group_id=group_id,msg_id='m1',timestamp=datetime.now(timezone.utc),collected_at=datetime.now(timezone.utc),
        message_type='file',text='[图片]' if kind=='image' else '[文件] '+name,
        content_json={'attachments':[{'key':'a1','kind':kind,'name':name,'path':str(path),'md5':hashlib.md5(content).hexdigest()}]})
    ar.ingest([msg]);return ar,msg,path


def test_selected_group_files_are_cached_without_changing_original(tmp_path):
    ar,msg,path=setup_message(tmp_path)
    service=AttachmentService(ar)
    result=service.prepare([msg])
    assert 'hello attachment' in result[0].text
    assert ar.connection.execute('SELECT text FROM messages').fetchone()[0]==msg.text
    path.unlink()
    assert 'hello attachment' in service.prepare([msg])[0].text
    ar.connection.close();ar=Archive.open(tmp_path/'a.sqlite')
    assert 'hello attachment' in cached_messages(ar,[msg])[0].text
    ar.upsert_groups([GroupConfig(group_id=11,name='合成群')])
    assert cached_messages(ar,[msg])[0].text==msg.text


def test_changed_source_digest_never_reuses_cached_text(tmp_path):
    ar,msg,path=setup_message(tmp_path)
    AttachmentService(ar).prepare([msg])
    ref={**msg.content_json['attachments'][0],'md5':'1'*32}
    changed=msg.model_copy(update={'content_json':{'attachments':[ref]}})
    result=cached_messages(ar,[changed],canonicalize=False)[0]
    assert 'hello attachment' not in result.text
    assert result.content_json['_attachment_unavailable']


def test_closed_group_never_opens_source(tmp_path):
    ar,msg,path=setup_message(tmp_path,enabled=False)
    result=AttachmentService(ar).prepare([msg])
    assert result[0].text==msg.text
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments').fetchone()[0]==0


def test_image_cache_lookup_uses_exact_account_digest_and_original(monkeypatch,tmp_path):
    from types import SimpleNamespace
    image=tmp_path/'i.png';Image.new('RGB',(30,30)).save(image)
    ar,msg,path=setup_message(tmp_path,kind='image',name='图片.png',content=image.read_bytes())
    digest=msg.content_json['attachments'][0]['md5']
    cache=tmp_path/'Documents'/'Tencent Files'/'42'/'nt_qq'/'nt_data'/'Pic'/'2026-10'/'Ori'
    cache.mkdir(parents=True);path.rename(cache/(digest+'.png'))
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cfg=SimpleNamespace(ntqq=SimpleNamespace(qq_number=42,enabled=False))
    output=AttachmentService(ar,cfg,vision=lambda _: {'text':'已核实图片','truncated':False}).prepare([msg])
    assert '已核实图片' in output[0].text


def test_new_attachments_are_read_before_missing_retries(tmp_path):
    ar,msg,path=setup_message(tmp_path);path.unlink()
    refs=[]
    for index in range(20):refs.append({**msg.content_json['attachments'][0],'key':str(index)})
    first=msg.model_copy(update={'content_json':{'attachments':refs}});ar.ingest([first])
    AttachmentService(ar).prepare([first])
    fresh=tmp_path/'fresh.txt';fresh.write_text('new useful content')
    second=msg.model_copy(update={'msg_id':'fresh','content_json':{'attachments':[{'key':'new','kind':'file','name':'fresh.txt','path':str(fresh),'md5':hashlib.md5(fresh.read_bytes()).hexdigest()}]}});ar.ingest([second])
    result=AttachmentService(ar).prepare([first,second],retry=True)
    assert 'new useful content' in result[1].text


def test_retries_rotate_so_older_downloads_are_not_starved(tmp_path):
    ar,msg,path=setup_message(tmp_path);path.unlink()
    refs=[{**msg.content_json['attachments'][0],'key':str(i),'path':str(tmp_path/f'{i}.txt')} for i in range(21)]
    msg=msg.model_copy(update={'content_json':{'attachments':refs}});ar.ingest([msg])
    service=AttachmentService(ar);service.prepare([msg]);service.prepare([msg])
    (tmp_path/'20.txt').write_bytes(b'hello attachment')
    service.prepare([msg],retry=True)
    service.prepare([msg],retry=True)
    row=ar.connection.execute("SELECT status FROM message_attachments WHERE attachment_key='20'").fetchone()
    assert row['status']=='success'


def test_source_hash_mismatch_does_not_expose_contents(tmp_path):
    ar,msg,path=setup_message(tmp_path)
    path.write_text('secret not from this chat',encoding='utf-8')
    AttachmentService(ar).prepare([msg])
    row=ar.connection.execute('SELECT status,text FROM message_attachments').fetchone()
    assert row['status']=='error' and row['text']==''
    assert 'secret' not in cached_messages(ar,[msg])[0].text


def test_image_uses_vision_once_with_base64_and_source_scope(tmp_path):
    path=tmp_path/'合成.png';Image.new('RGB',(40,40),'white').save(path)
    ar,msg,_=setup_message(tmp_path,kind='image',name='图.png',content=path.read_bytes())
    calls=[]
    def vision(blocks):calls.append(blocks);return {'text':'测试图中文字：合成内容','truncated':False}
    service=AttachmentService(ar,vision=vision)
    assert '合成内容' in service.prepare([msg])[0].text
    service.prepare([msg]);assert len(calls)==1
    assert calls[0][1]['type']=='image_url' and calls[0][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert 'path' not in calls[0][0]['text']


def test_budget_paused_image_resumes_without_losing_local_files(tmp_path):
    from qq_digest.ai.client import AILimitExceeded
    path=tmp_path/'合成.png';Image.new('RGB',(40,40),'white').save(path)
    ar,msg,_=setup_message(tmp_path,kind='image',name='图.png',content=path.read_bytes())
    def blocked(_):raise AILimitExceeded('本月 AI 调用已达上限，可调整上限或下月继续')
    service=AttachmentService(ar,vision=blocked);service.prepare([msg])
    row=ar.connection.execute('SELECT status,error FROM message_attachments').fetchone()
    assert row['status']=='budget_paused' and '上限' in row['error']
    local=tmp_path/'local.txt';local.write_text('local useful content')
    ref={'key':'local','kind':'file','name':'local.txt','path':str(local),'md5':hashlib.md5(local.read_bytes()).hexdigest()}
    msg=msg.model_copy(update={'content_json':{'attachments':msg.content_json['attachments']+[ref]}});ar.ingest([msg])
    assert 'local useful content' in service.prepare([msg])[0].text
    service.vision=lambda _: {'text':'恢复识别','truncated':False}
    assert '恢复识别' in service.prepare([msg])[0].text


def test_missing_file_retry_and_truncation(tmp_path):
    ar,msg,path=setup_message(tmp_path,content=('正文'*7000).encode())
    original=path.read_bytes();path.unlink();service=AttachmentService(ar)
    service.prepare([msg]);assert ar.connection.execute('SELECT status FROM message_attachments').fetchone()[0]=='missing'
    path.write_bytes(original);result=service.prepare([msg],retry=True)
    row=ar.connection.execute('SELECT status,text FROM message_attachments').fetchone()
    assert row['status']=='partial' and len(row['text'])<=12000
    assert '截断' in result[0].text


def test_bad_paths_and_unsupported_files_never_execute(tmp_path):
    ar,msg,path=setup_message(tmp_path,name='run.exe',content=b'MZno execution')
    AttachmentService(ar).prepare([msg])
    assert ar.connection.execute('SELECT status FROM message_attachments').fetchone()[0]=='unsupported'
    ar,msg,path=setup_message(tmp_path,name='说明.txt')
    msg.content_json['attachments'][0]['path']='//server/share/file.txt'
    AttachmentService(ar).prepare([msg],retry=True)
    assert ar.connection.execute('SELECT status FROM message_attachments').fetchone()[0]=='error'


def test_attachment_batch_limit_and_group_deletion(tmp_path):
    ar,msg,_=setup_message(tmp_path)
    messages=[msg.model_copy(update={'msg_id':f'm{i}'}) for i in range(23)]
    ar.ingest(messages);service=AttachmentService(ar)
    result=service.prepare(messages)
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments WHERE status IN (\'success\',\'partial\')').fetchone()[0]==20
    service.prepare(messages)
    assert 'hello attachment' in cached_messages(ar,messages)[-1].text
    ar.delete_group(11)
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments').fetchone()[0]==0


def test_vision_failure_keeps_normal_message_and_retries(tmp_path):
    png=tmp_path/'source.png';Image.new('RGB',(10,10)).save(png)
    ar,msg,_=setup_message(tmp_path,kind='image',name='图.png',content=png.read_bytes())
    def broken(_):raise RuntimeError('private key / path should not be logged')
    result=AttachmentService(ar,vision=broken).prepare([msg])
    assert msg.text in result[0].text
    assert 'private key' not in str(ar.connection.execute('SELECT error FROM message_attachments').fetchone()[0])


def test_multiple_attachments_do_not_cross_groups(tmp_path):
    ar,msg,path=setup_message(tmp_path)
    ar.upsert_groups([GroupConfig(group_id=12,name='未开放群')])
    other=msg.model_copy(update={'group_id':12});ar.ingest([other])
    AttachmentService(ar).prepare([msg,other])
    assert 'hello attachment' in cached_messages(ar,[msg])[0].text
    assert cached_messages(ar,[other])[0].text==other.text


def test_vision_uses_single_cockpit_connection_and_closes_client(tmp_path,monkeypatch):
    from tests.test_ai_settings import make_config
    import httpx
    cfg=make_config(tmp_path)
    cfg.ai.base_url='http://127.0.0.1:62406/v1'
    cfg.ai.model='gpt-6.1-sol'
    monkeypatch.setenv(cfg.ai.api_key_env,'synthetic-cockpit-key')
    seen=[]
    def post(client,path,**kwargs):
        seen.append((str(client.base_url),kwargs['json'],client.headers['authorization']))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"text":"合成识别结果","truncated":false}'}}]},request=httpx.Request('POST','http://127.0.0.1:62406/v1/chat/completions'))
    monkeypatch.setattr(httpx.Client,'post',post)
    ar,_,_=setup_message(tmp_path)
    image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG')
    assert AttachmentService(ar,cfg)._vision([image.getvalue()],'合成.png')==('合成识别结果',False)
    assert seen[0][0].rstrip('/')=='http://127.0.0.1:62406/v1'
    assert seen[0][1]['model']=='gpt-6.1-sol'
    assert seen[0][1]['messages'][0]['content'][1]['type']=='image_url'
    assert seen[0][2]=='Bearer synthetic-cockpit-key'


@pytest.mark.parametrize('scan',[False,True])
def test_image_and_scanned_file_limit_pause_before_http(tmp_path,monkeypatch,respx_mock,scan):
    from qq_digest.config import Config,AIConfig,SecurityConfig
    from qq_digest.ai_usage import usage_for
    data=io.BytesIO();Image.new('RGB',(8,8)).save(data,format='PNG')
    ar,msg,path=setup_message(tmp_path,kind='file' if scan else 'image',name='合成.pdf' if scan else '合成.png',
        content=b'synthetic PDF' if scan else data.getvalue())
    if scan:
        monkeypatch.setattr('qq_digest.attachments.read_document',lambda *args:('',False,[data.getvalue()]))
    cfg=Config(data_dir=tmp_path,archive_path=tmp_path/'a.sqlite',report_dir=tmp_path/'reports',knowledge_dir=tmp_path/'knowledge',
        work_dir=tmp_path/'work',log_dir=tmp_path/'logs',security=SecurityConfig(web_password_hash='fake'),
        ai=AIConfig(base_url='https://api.deepseek.com',model='synthetic',api_key_env='SYNTHETIC_KEY',provider_priority=['compatible']))
    monkeypatch.setenv('SYNTHETIC_KEY','synthetic')
    service=usage_for(cfg);service.update(1,[],0);service.reserve(cfg.ai.base_url,cfg.ai.model,'summary',30)
    route=respx_mock.post(cfg.ai.base_url+'/chat/completions').mock(side_effect=AssertionError('limit must block HTTP'))
    AttachmentService(ar,cfg).prepare([msg])
    assert route.call_count==0 and ar.connection.execute('SELECT status FROM message_attachments').fetchone()[0]=='budget_paused'
