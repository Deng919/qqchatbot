import hashlib
import base64
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


def indexed_images(db_dir, rows):
    with sqlite3.connect(db_dir/'files_in_chat.db') as connection:
        connection.execute('CREATE TABLE files_in_chat_table ('
            '"45001" INTEGER PRIMARY KEY,"40001" INTEGER,"40021" TEXT,'
            '"40020" TEXT,"40050" INTEGER,"40010" INTEGER,"45002" INTEGER,'
            '"45402" TEXT,"45403" TEXT,"45406" TEXT)')
        connection.executemany('INSERT INTO files_in_chat_table VALUES (?,?,?,?,?,?,?,?,?,?)',rows)


def collect_index_window(db_dir):
    return list(NTQQCollector(db_dir).collect(100,
        datetime(2025,10,7,tzinfo=timezone.utc),datetime(2025,10,9,tzinfo=timezone.utc)))


def test_fts_collection_recovers_exact_indexed_image_when_message_snapshot_is_stale(ntqq_fts_dbs):
    indexed_images(ntqq_fts_dbs,[(1,9902,'100','u_sender',1759851750,2,2,
        'photo.png','',base64.b64encode(b'x'*16).decode())])

    images=[m for m in collect_index_window(ntqq_fts_dbs) if m.msg_id=='9902']

    assert len(images)==1
    assert images[0].text=='[图片]'
    assert images[0].message_type=='file'
    assert images[0].content_json['sender_uid']=='u_sender'
    assert images[0].content_json['attachments']==[{
        'key':'image:'+'78'*16+':0','kind':'image','name':'photo.png','path':'','md5':'78'*16}]


def test_indexed_images_merge_only_exact_group_message_and_timestamp(ntqq_fts_dbs):
    digest=base64.b64encode(b'x'*16).decode()
    indexed_images(ntqq_fts_dbs,[
        (1,8101,'100','u_sender',1759851722,2,2,'photo.png','',digest),
        (2,8101,'100','u_sender',1759851722,2,2,'other.png','',base64.b64encode(b'y'*16).decode()),
        (3,8102,'100','u_sender',1759851724,2,2,'wrong-time.png','',digest),
        (4,8103,'200','u_sender',1759851724,2,2,'wrong-group.png','',digest),
        (5,8104,'100','u_sender',1759851725,1,2,'not-group.png','',digest),
        (6,8105,'100','u_sender',1759851726,2,4,'not-image.png','',digest),
        (7,9903,'100','u_sender',1759851750,2,2,'invalid.png','','invalid'),
        (8,9904,'100','u_sender',1759851750,2,2,'short.png','',base64.b64encode(b'x').decode()),
    ])

    messages={m.msg_id:m for m in collect_index_window(ntqq_fts_dbs)}

    assert messages['8101'].text=='普通正文'
    assert [ref['md5'] for ref in messages['8101'].content_json['attachments']]==['78'*16,'79'*16]
    assert all(not any(ref['kind']=='image' for ref in messages[mid].content_json.get('attachments',[]))
        for mid in ['8102','8103','8104','8105'])
    assert '9903' not in messages and '9904' not in messages


def test_indexed_image_recovery_survives_unreadable_message_metadata(ntqq_fts_dbs,monkeypatch):
    from qq_digest.collector.base import CollectionIncompleteError
    indexed_images(ntqq_fts_dbs,[(1,9902,'100','u_sender',1759851750,2,2,
        'photo.png','',base64.b64encode(b'x'*16).decode())])
    def broken(*args):
        raise CollectionIncompleteError('broken metadata')
    monkeypatch.setattr(NTQQCollector,'_scan_metadata',broken)

    messages={m.msg_id:m for m in collect_index_window(ntqq_fts_dbs)}

    assert messages['9902'].content_json['attachments'][0]['md5']=='78'*16
    assert messages['8101'].content_json['attachment_source_unavailable'] is True


def test_indexed_image_does_not_replace_or_duplicate_verified_blob_reference(ntqq_fts_dbs):
    with sqlite3.connect(ntqq_fts_dbs/'nt_msg.db') as connection:
        inner=make_varint_field(45002,2)+make_bytes_field(45302,b'x'*16)
        connection.execute('INSERT INTO group_msg_table VALUES (?,?,?,?,?,?)',
            (9902,'u_sender','100',100,1759851750,make_bytes_field(40800,inner)))
    indexed_images(ntqq_fts_dbs,[
        (1,9902,'100','u_sender',1759851750,2,2,'photo.png','',base64.b64encode(b'x'*16).decode()),
        (2,9902,'100','u_sender',1759851750,2,2,'stale.png','',base64.b64encode(b'y'*16).decode())])

    message=next(m for m in collect_index_window(ntqq_fts_dbs) if m.msg_id=='9902')

    assert len(message.content_json['attachments'])==1
    assert message.content_json['attachments'][0]['md5']=='78'*16


def test_indexed_image_sender_resolves_profile_uid(ntqq_fts_dbs):
    with sqlite3.connect(ntqq_fts_dbs/'profile_info.db') as connection:
        connection.execute('CREATE TABLE profile_info_v6 ("1000" TEXT,"1002" INTEGER,"20002" TEXT)')
        connection.execute('INSERT INTO profile_info_v6 VALUES (?,?,?)',('u_sender',45678,'昵称'))
    indexed_images(ntqq_fts_dbs,[(1,9902,'100','u_sender',1759851750,2,2,
        'photo.png','',base64.b64encode(b'x'*16).decode())])

    message=next(m for m in collect_index_window(ntqq_fts_dbs) if m.msg_id=='9902')

    assert message.sender_qq==45678 and message.content_json['sender_name']=='昵称'


def test_broken_image_index_preserves_searchable_messages_as_incomplete(ntqq_fts_dbs):
    (ntqq_fts_dbs/'files_in_chat.db').write_bytes(b'broken source')

    messages={m.msg_id:m for m in collect_index_window(ntqq_fts_dbs)}

    assert messages['8101'].text=='普通正文'
    assert messages['8101'].content_json['attachment_source_unavailable'] is True
