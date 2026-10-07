import hashlib
import sqlite3
from datetime import datetime,timezone

from qq_digest.collector.protobuf import make_bytes_field,make_varint_field
from qq_digest.attachment_sources import attachment_refs
from qq_digest.archive import Archive
from qq_digest.models import GroupConfig,NormalizedMessage
from tests.test_ntqq_collector import ntqq_dbs,ntqq_fts_dbs
from qq_digest.collector.ntqq import NTQQCollector


def file_blob(name,path,md5):
    inner=make_varint_field(45002,3)+make_bytes_field(45402,name.encode())+make_bytes_field(45403,path.encode())+make_bytes_field(45406,bytes.fromhex(md5))
    return make_bytes_field(40800,inner)


def test_extracts_multiple_images_and_file_with_exact_digest():
    inner=make_varint_field(45002,2)+make_bytes_field(45301,b'photo.png')+make_bytes_field(45302,b'x'*16)+make_bytes_field(45303,b'D:\\QQ\\photo.png')
    data=make_bytes_field(40800,inner)+file_blob('资料.txt','D:\\QQ\\资料.txt','ab'*16)
    refs=attachment_refs(data)
    assert [r['kind'] for r in refs]==['image','file']
    assert refs[0]['md5']==('78'*16) and refs[1]['md5']=='ab'*16
    assert refs[1]['name']=='资料.txt'


def test_current_qq_image_fields_keep_verified_digest_without_local_path():
    inner=make_varint_field(45002,2)+make_bytes_field(45402,b'photo.png')+make_bytes_field(45406,b'x'*16)+make_bytes_field(45802,b'https://qq.example/image')
    ref=attachment_refs(make_bytes_field(40800,inner))[0]
    assert ref['md5']=='78'*16 and ref['name']=='photo.png' and ref['path']==''


def test_attachment_metadata_refresh_does_not_replace_original(tmp_path):
    ar=Archive.open(tmp_path/'a.sqlite');ar.upsert_groups([GroupConfig(group_id=11,name='群')])
    msg=NormalizedMessage(group_id=11,msg_id='m',timestamp=datetime.now(timezone.utc),collected_at=datetime.now(timezone.utc),text='[图片]',message_type='file')
    ar.ingest([msg]);incoming=msg.model_copy(update={'content_json':{'attachments':[{'key':'a','kind':'image','md5':'ab'*16,'name':'图.png','path':'D:/QQ/图.png'}]}})
    ar.ingest([incoming])
    reread=ar.messages_between(11,msg.timestamp,msg.timestamp)[0]
    assert reread.text=='[图片]' and reread.content_json['attachments'][0]['md5']=='ab'*16


def test_source_path_refresh_preserves_same_digest_reading(tmp_path):
    from tests.test_attachments import setup_message
    from qq_digest.attachments import AttachmentService
    ar,msg,_=setup_message(tmp_path);AttachmentService(ar).prepare([msg])
    ref={**msg.content_json['attachments'][0],'path':'D:/QQ/new-location.txt'}
    ar.ingest([msg.model_copy(update={'content_json':{'attachments':[ref]}})])
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments').fetchone()[0]==1
    placeholder={**ref,'key':'file:unknown:0','path':'','md5':''}
    ar.ingest([msg.model_copy(update={'content_json':{'attachments':[placeholder]}})])
    assert ar.messages_between(11,msg.timestamp,msg.timestamp)[0].content_json['attachments'][0]['md5']==ref['md5']
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments').fetchone()[0]==1
    changed={**ref,'md5':'aa'*16}
    ar.ingest([msg.model_copy(update={'content_json':{'attachments':[changed]}})])
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments').fetchone()[0]==0


def test_temporary_source_fallback_cannot_add_false_download_notice(tmp_path):
    from tests.test_attachments import setup_message
    from qq_digest.attachments import AttachmentService
    ar,msg,_=setup_message(tmp_path);service=AttachmentService(ar);service.prepare([msg])
    fallback=msg.model_copy(update={'content_json':{'attachments':[{'key':'file:unknown:0','kind':'file','name':'说明.txt','path':'','md5':''}]}})
    ar.ingest([fallback]);output=service.prepare([fallback])
    assert 'hello attachment' in output[0].text and '请先' not in output[0].text
    ar.ingest([msg]);service.prepare([msg])
    assert ar.connection.execute('SELECT COUNT(*) FROM message_attachments').fetchone()[0]==1


def test_fts_collection_also_retains_images_from_message_database(ntqq_fts_dbs):
    source=ntqq_fts_dbs/'nt_msg.db'
    with sqlite3.connect(source) as c:
        inner=make_varint_field(45002,2)+make_bytes_field(45302,b'x'*16)+make_bytes_field(45303,b'D:\\QQ\\image.png')
        c.execute('INSERT INTO group_msg_table("40001","40020","40021","40027","40050","40800") VALUES(?,?,?,?,?,?)',
            (9901,'u_sender','100',100,1759851750,make_bytes_field(40800,inner)))
    result=list(NTQQCollector(ntqq_fts_dbs).collect(100,datetime(2025,10,7,tzinfo=timezone.utc),datetime(2025,10,9,tzinfo=timezone.utc)))
    image=next(m for m in result if m.msg_id=='9901')
    assert image.text=='[图片]' and image.content_json['attachments'][0]['kind']=='image'
    assert len({m.msg_id for m in result})==len(result)
