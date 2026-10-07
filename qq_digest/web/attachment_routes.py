"""Authenticated attachment results and bounded group-scoped rereading."""
import asyncio
import json

from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel

from ..attachments import AttachmentService,VERSION,allowed,policy
from ..operations import OperationBusy


class ReadAttachments(BaseModel):
    model_config={'extra':'forbid'}


def add_attachment_routes(app,*,archive,config,require_login,operations):
    def messages(ar,gid):
        return ar.connection.execute('''SELECT * FROM messages WHERE group_id=?
            AND (message_type='file' OR json_type(content_json,'$.attachments')='array') ORDER BY timestamp DESC,msg_id DESC LIMIT 200''',(gid,)).fetchall()

    def listing(ar,gid):
        group=policy(ar,gid)
        if group is None:raise HTTPException(404,'群聊不存在')
        cache={(row['msg_id'],row['attachment_key']):row for row in ar.connection.execute('SELECT * FROM message_attachments WHERE group_id=?',(gid,))}
        items=[]
        for row in messages(ar,gid):
            try:refs=json.loads(row['content_json']).get('attachments',[])
            except (ValueError,TypeError):refs=[]
            if not isinstance(refs,list):continue
            for ref in refs[:100]:
                if not isinstance(ref,dict) or ref.get('kind') not in ('image','file'):continue
                old=cache.get((row['msg_id'],str(ref.get('key') or '')))
                current=old is not None and old['source_md5']==str(ref.get('md5') or '').lower() and old['version']==VERSION
                status=old['status'] if current else 'pending' if allowed(group,ref['kind']) else 'disabled'
                items.append({'msg_id':row['msg_id'],'key':ref.get('key',''),'kind':ref['kind'],'name':str(ref.get('name','附件'))[:200],
                    'at':row['timestamp'],'status':status,'text':old['text'] if current else '', 'error':old['error'] if current else ''})
        return {'items':items[:100],'counts':{state:sum(item['status']==state for item in items) for state in ('success','partial','pending','missing','unsupported','error','disabled')},
            'limited':len(items)>100,'read_images':bool(group['read_images']),'read_files':bool(group['read_files'])}

    @app.get('/api/groups/{gid}/attachments',dependencies=[Depends(require_login)])
    async def get(request:Request,gid:int):return listing(archive(request),gid)

    @app.post('/api/groups/{gid}/attachments/read',dependencies=[Depends(require_login)])
    async def read(request:Request,gid:int,payload:ReadAttachments):
        def worker():
            with operations.claim('collect'):
                ar=archive(request);group=policy(ar,gid)
                if group is None:raise HTTPException(404,'群聊不存在')
                if not (allowed(group,'image') or allowed(group,'file')):raise HTTPException(409,'请先保存并开启这个群的附件读取')
                source=[ar._row_to_message(row) for row in messages(ar,gid)]
                AttachmentService(ar,config(request)).prepare(source,retry=True)
                return listing(ar,gid)
        try:return await asyncio.to_thread(worker)
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc
