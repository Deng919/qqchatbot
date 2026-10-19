"""Bounded source units; forwarded identities come from metadata, never AI guesses."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def qq_number(value):
    if isinstance(value,bool) or not isinstance(value,(int,str)):return None
    try:number=int(value)
    except (ValueError,TypeError):return None
    return number if 0<number<2**63 else None


def source_units(messages,blocked=()):
    blocked=set(blocked);result=[]
    for message in messages:
        if message.sender_qq in blocked:continue
        outer=message.timestamp.astimezone(timezone.utc).isoformat()
        metadata=message.content_json
        def visit(node,path,depth=0,forwarded=False):
            if len(result)>=20000:return
            nodes=node.get('forward_messages')
            if isinstance(nodes,list) and depth<5:
                for index,child in enumerate(nodes[:100]):
                    if isinstance(child,dict):visit(child,path+'.'+str(index),depth+1,True)
                return
            seller=qq_number(node.get('sender_qq')) if forwarded else message.sender_qq
            if seller in blocked:return
            text=str(node.get('text') or '')[:16000]
            image_failed=bool(node.get('_attachment_unavailable') or re.search(r'\[附件 [^\n]*\]',text))
            text=re.sub(r'\n?\[附件 [^\n]*\]','',text)
            at=outer;date_unknown=False
            if forwarded:
                try:
                    original=datetime.fromisoformat(str(node.get('timestamp','')))
                    if original.tzinfo is None or original>message.timestamp:raise ValueError()
                    at=original.astimezone(timezone.utc).isoformat()
                except (ValueError,TypeError):date_unknown=True
            is_forward=forwarded or message.message_type=='forward'
            state='ready'
            if message.message_type=='forward' and not forwarded:state='forward_unavailable'
            if is_forward and not text.strip():state='forward_unavailable'
            if text.strip()=='[图片]':state='image_unavailable'
            unit={'key':digest([message.group_id,message.msg_id,path]),'group_id':message.group_id,
                  'msg_id':message.msg_id,'path':path,'seller_qq':seller,'seller_name':str(node.get('sender_name') or '')[:160],
                  'forwarder_qq':message.sender_qq if is_forward else None,
                  'published_at':at,'forwarded_at':outer if is_forward else None,'text':text,'state':state,
                  'incomplete':date_unknown or bool(metadata.get('attachment_source_unavailable')),
                  'attachments':node.get('attachments',[]) if isinstance(node.get('attachments',[]),list) else []}
            if date_unknown:unit['original_time_unknown']=True
            unit['incomplete']=unit['incomplete'] or image_failed
            if image_failed:unit['images_unavailable']=True
            if path!='0':
                unit['attachments']=[{**ref,'key':digest([path,ref.get('key')])} for ref in unit['attachments'][:100] if isinstance(ref,dict)]
            unit['fingerprint']=digest(unit);result.append(unit)
            if image_failed and state=='ready':
                issue={**unit,'key':digest([message.group_id,message.msg_id,path+'.image']),'path':path+'.image','attachment_issue':True,'state':'image_unavailable','text':'[图片]'}
                issue['fingerprint']=digest({key:value for key,value in issue.items() if key!='fingerprint'});result.append(issue)
        visit({**metadata,'text':message.text},'0')
    return result
