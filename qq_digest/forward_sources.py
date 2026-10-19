"""Recover complete embedded forward nodes; summary-card previews are not records."""
from __future__ import annotations

import json
from datetime import timezone

from .collector.protobuf import parse_protobuf,_get_varint
from .secondhand_evidence import qq_number


def forward_records(blob):
    result=[]
    if not blob or len(blob)>4*1024*1024:return result
    def clean(node,depth=0):
        if not isinstance(node,dict) or depth>4:return None
        data=node.get('data',node)
        if not isinstance(data,dict):return None
        children=data.get('forward_messages',data.get('messages',data.get('nodes')))
        if isinstance(children,list):return {'forward_messages':[entry for child in children[:100] if (entry:=clean(child,depth+1))]}
        text=data.get('text')
        if not isinstance(text,str):
            content=data.get('content')
            if isinstance(content,str):text=content
            elif isinstance(content,list):text=''.join(str(piece.get('data',{}).get('text','')) for piece in content[:100] if isinstance(piece,dict) and piece.get('type')=='text' and isinstance(piece.get('data'),dict))
        refs=[{key:ref.get(key,'') for key in ('key','kind','name','path','md5')} for ref in data.get('attachments',[])[:100] if isinstance(ref,dict) and ref.get('kind')=='image'] if isinstance(data.get('attachments'),list) else []
        if not text and not refs:return None
        return {'text':(text or '[图片]')[:16000],'attachments':refs,'sender_qq':qq_number(data.get('sender_qq',data.get('user_id'))),
                'sender_name':str(data.get('sender_name',data.get('nickname','')))[:160],
                'timestamp':str(data.get('timestamp',''))[:60]}
    for wire,element in parse_protobuf(blob).get(40800,[]):
        if wire!='bytes':continue
        fields=parse_protobuf(element)
        if _get_varint(fields,45002) not in (6,8,17):continue
        for kind,value in fields.get(48271,[]):
            if kind!='bytes' or len(value)>1024*1024:continue
            try:payload=json.loads(value.decode('utf-8'))
            except (ValueError,UnicodeError):continue
            if not isinstance(payload,dict):continue
            nodes=payload.get('forward_messages',payload.get('messages',payload.get('nodes')))
            if not isinstance(nodes,list):continue
            for node in nodes[:100]:
                record=clean(node)
                if record:result.append(record)
    return result[:100]


def refresh_forward_metadata(connection,message):
    records=message.content_json.get('forward_messages')
    if not isinstance(records,list) or not records:return
    row=connection.execute('SELECT timestamp,content_json FROM messages WHERE group_id=? AND msg_id=?',(message.group_id,message.msg_id)).fetchone()
    if row is None or row['timestamp']!=message.timestamp.astimezone(timezone.utc).isoformat():return
    metadata=json.loads(row['content_json'])
    if metadata.get('forward_messages')==records:return
    metadata['forward_messages']=records
    connection.execute('UPDATE messages SET content_json=? WHERE group_id=? AND msg_id=?',(json.dumps(metadata,ensure_ascii=False),message.group_id,message.msg_id))
