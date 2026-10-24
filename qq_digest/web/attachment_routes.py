"""Authenticated attachment results and bounded group-scoped rereading."""
import json

from fastapi import Depends,HTTPException,Request,Response,Query
from pydantic import BaseModel

from ..attachments import AttachmentService,VERSION,allowed,policy
from ..operations import OperationBusy
from .worker_threads import guarded_to_thread


class ReadAttachments(BaseModel):
    model_config={'extra':'forbid'}


def attachment_messages(ar,gid):
    return ar.connection.execute('''SELECT * FROM messages WHERE group_id=?
        AND (message_type='file' OR json_type(content_json,'$.attachments')='array') ORDER BY timestamp DESC,msg_id DESC LIMIT 200''',(gid,)).fetchall()

def attachment_listing(ar,gid):
    group=policy(ar,gid)
    if group is None:raise HTTPException(404,'群聊不存在')
    cache={(row['msg_id'],row['attachment_key']):row for row in ar.connection.execute('SELECT * FROM message_attachments WHERE group_id=?',(gid,))}
    items=[]
    for row in attachment_messages(ar,gid):
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
    return {'items':items[:100],'counts':{state:sum(item['status']==state for item in items) for state in ('success','partial','pending','missing','unsupported','error','disabled','budget_paused')},
        'enabled':bool(group['enabled']),'limited':len(items)>100,'read_images':bool(group['read_images']),'read_files':bool(group['read_files'])}



def add_attachment_routes(app,*,archive,config,require_login,operations):
    from ..message_images import image_source,message_images

    def listing(ar,gid,msg_id):
        try:return message_images(ar,gid,msg_id)
        except LookupError as exc:raise HTTPException(404,str(exc)) from exc

    @app.get('/api/groups/{gid}/messages/{msg_id}/images',dependencies=[Depends(require_login)])
    async def images(request:Request,gid:int,msg_id:str):return listing(archive(request),gid,msg_id)

    @app.get('/api/groups/{gid}/messages/{msg_id}/images/original',dependencies=[Depends(require_login)])
    async def original(request:Request,gid:int,msg_id:str,key:str=Query(max_length=150),preview:bool=False):
        ar=archive(request);listing(ar,gid,msg_id)
        group=policy(ar,gid)
        if not group or not group['enabled']:raise HTTPException(403,'这个群已停用')
        _,refs=image_source(ar,gid,msg_id)
        ref=next((r for r in refs if r['key']==key),None)
        if ref is None:raise HTTPException(404,'图片来源不存在')
        market=bool(ar.connection.execute('SELECT 1 FROM market_groups WHERE group_id=?',(gid,)).fetchone())
        if preview and not market:raise HTTPException(404,'此来源不支持二手图片预览')
        reader=AttachmentService(ar,config(request))
        try:data,mime=await guarded_to_thread(reader.preview_image if preview else reader.original_image,ref,**({'download':True} if market else {}))
        except (OSError,ValueError,NotImplementedError) as exc:raise HTTPException(404,'原图未下载或无法核实来源') from exc
        return Response(data,media_type=mime,headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @app.post('/api/groups/{gid}/messages/{msg_id}/images/{key}/read',dependencies=[Depends(require_login)])
    async def read_image(request:Request,gid:int,msg_id:str,key:str,payload:ReadAttachments):
        def worker():
            with operations.claim('collect'):
                ar=archive(request);listing(ar,gid,msg_id)
                if not allowed(policy(ar,gid),'image'):raise HTTPException(409,'请先开启这个群的图片识别')
                message,refs=image_source(ar,gid,msg_id)
                ref=next((r for r in refs if r['key']==key),None)
                if ref is None:raise HTTPException(404,'图片来源不存在')
                virtual=message.model_copy(update={'message_type':'file','content_json':{'attachments':[ref]}})
                AttachmentService(ar,config(request)).prepare([virtual],retry=True,canonicalize=False)
                return listing(ar,gid,msg_id)
        try:return await guarded_to_thread(worker)
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/api/groups/{gid}/attachments',dependencies=[Depends(require_login)])
    async def get(request:Request,gid:int):return attachment_listing(archive(request),gid)

    @app.post('/api/groups/{gid}/attachments/read',dependencies=[Depends(require_login)])
    async def read(request:Request,gid:int,payload:ReadAttachments):
        def worker():
            with operations.claim('collect'):
                ar=archive(request);group=policy(ar,gid)
                if group is None:raise HTTPException(404,'群聊不存在')
                if not group['enabled']:raise HTTPException(409,'这个群已停用，请先关注该群再读取附件')
                if not (allowed(group,'image') or allowed(group,'file')):raise HTTPException(409,'请先保存并开启这个群的附件读取')
                source=[ar._row_to_message(row) for row in attachment_messages(ar,gid)]
                AttachmentService(ar,config(request)).prepare(source,retry=True)
                return attachment_listing(ar,gid)
        try:return await guarded_to_thread(worker)
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc
