"""Recover attachment references from message elements, never from chat text."""
from __future__ import annotations

import json
import re
from datetime import timezone
from pathlib import PureWindowsPath

from .collector.protobuf import parse_protobuf,_get_bytes,_get_varint

_PATH=re.compile(r'[A-Za-z]:[\\/][^\x00-\x1f<>|"*?]{1,600}?\.(?:png|jpe?g|gif|webp|pdf|docx?|xlsx?|pptx?|txt|md|csv|json|log|exe|zip)(?=$|[\x00-\x1f"\s])',re.I)


def _paths(data,depth=0):
    result=_PATH.findall(data.decode('utf-8',errors='replace'))
    if depth<4:
        for values in parse_protobuf(data).values():
            for wire,value in values:
                if wire=='bytes' and value and len(value)<=20000:result.extend(_paths(value,depth+1))
    return list(dict.fromkeys(result))[:8]


def attachment_refs(blob):
    refs=[]
    for wire,data in parse_protobuf(blob or b'').get(40800,[]):
        if wire!='bytes':continue
        fields=parse_protobuf(data);kind=_get_varint(fields,45002)
        if kind not in (2,3):continue
        image=kind==2;hash_data=_get_bytes(fields,45406) or (_get_bytes(fields,45302) if image else None)
        md5=hash_data.hex() if hash_data and len(hash_data)==16 else ''
        path_values=[]
        numbers=[45403]+([number for number in fields if 45300<=number<45400] if image else [])
        for number in numbers:
            for entry,value in fields.get(number,[]):
                if entry=='bytes':path_values.extend(_paths(value))
        path=next(iter(path_values),'')
        raw_name=_get_bytes(fields,45402) or (_get_bytes(fields,45301) if image else None) or b''
        name=raw_name.decode('utf-8',errors='replace')
        if not name or any(not c.isprintable() for c in name):name=PureWindowsPath(path).name or ('图片' if image else '文件')
        label='image' if image else 'file'
        refs.append({'key':f'{label}:{md5}:{len(refs)}','kind':label,'name':name[:200],'path':path,'md5':md5})
    return refs[:100]


def refresh_attachment_metadata(connection,message):
    refs=message.content_json.get('attachments')
    if not isinstance(refs,list) or not refs:return
    row=connection.execute('SELECT content_json,timestamp FROM messages WHERE group_id=? AND msg_id=?',(message.group_id,message.msg_id)).fetchone()
    if row is None or row['timestamp']!=message.timestamp.astimezone(timezone.utc).isoformat():return
    try:metadata=json.loads(row['content_json'])
    except (TypeError,ValueError):metadata={}
    previous=metadata.get('attachments',[])
    if not isinstance(previous,list):previous=[]
    refs=[ref for ref in refs if isinstance(ref,dict)]
    for index,ref in enumerate(refs):
        if not ref.get('md5') and index<len(previous) and isinstance(previous[index],dict):
            old=previous[index]
            if old.get('kind')==ref.get('kind') and old.get('md5'):refs[index]=old
    unchanged=metadata.get('attachments')==refs
    metadata['attachments']=refs
    identities={(str(ref.get('key') or ''),ref.get('kind'),str(ref.get('md5') or '').lower()) for ref in refs}
    for cached in connection.execute('SELECT attachment_key,kind,source_md5 FROM message_attachments WHERE group_id=? AND msg_id=?',(message.group_id,message.msg_id)).fetchall():
        if (cached['attachment_key'],cached['kind'],cached['source_md5']) not in identities:
            connection.execute('DELETE FROM message_attachments WHERE group_id=? AND msg_id=? AND attachment_key=?',(message.group_id,message.msg_id,cached['attachment_key']))
    if unchanged:return
    connection.execute('UPDATE messages SET content_json=? WHERE group_id=? AND msg_id=?',(json.dumps(metadata,ensure_ascii=False),message.group_id,message.msg_id))
