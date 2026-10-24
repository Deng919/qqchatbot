"""Authenticated marketplace reads and coordinated explicit refreshes."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt

from ..archive import Archive
from ..secondhand import SecondhandService,date_window,recent_window
from ..operations import OperationBusy
from .worker_threads import guarded_to_thread


class Favorite(BaseModel):
    model_config={'extra':'forbid'}
    favorite:StrictBool


class Block(BaseModel):
    model_config={'extra':'forbid'}
    qq:StrictInt=Field(gt=0,lt=2**63)


class Dismiss(BaseModel):
    model_config={'extra':'forbid'}
    item_ids:list[str]=Field(min_length=1,max_length=500)


class RestoreInterest(BaseModel):
    model_config={'extra':'forbid'}
    keys:list[str]=Field(min_length=1,max_length=500)


class Refresh(BaseModel):
    model_config={'extra':'forbid'}
    date_from:str|None=Field(default=None,max_length=10)
    date_to:str|None=Field(default=None,max_length=10)
    rebuild:StrictBool=False


def add_secondhand_routes(app,*,archive,config,templates,require_login,operations):
    timezone_name=config.summary.timezone if config else 'Asia/Shanghai'
    service=SecondhandService(archive,timezone_name)
    refresh_lock=Lock()
    progress={'running':False,'message':'','result':None}

    def source_images(group_id,msg_id):
        from ..secondhand_evidence import source_units
        row=archive.connection.execute('SELECT * FROM messages WHERE group_id=? AND msg_id=?',(group_id,msg_id)).fetchone()
        if row is None:return []
        units=source_units([archive._row_to_message(row)],service.blocked_ids())
        return [ref for unit in units if not unit.get('attachment_issue') for ref in unit['attachments'] if isinstance(ref,dict) and ref.get('kind')=='image']

    def continuous_image_sources(item):
        from ..secondhand_evidence import source_units
        # A source sequence proves who spoke between text and separately sent
        # pictures. Time or the same seller alone cannot establish continuity.
        if item.get('path')!='0' or item.get('forwarder_qq') or not item.get('seller_qq'):return []
        row=archive.connection.execute('SELECT * FROM messages WHERE group_id=? AND msg_id=?',(item['group_id'],item['msg_id'])).fetchone()
        if row is None:return []
        anchor=archive._row_to_message(row);seq=anchor.content_json.get('message_sequence')
        if type(seq) is not int or anchor.sender_qq!=item['seller_qq'] or anchor.message_type=='forward':return []
        start=(anchor.timestamp-timedelta(seconds=120)).isoformat();end=(anchor.timestamp+timedelta(seconds=120)).isoformat()
        rows=archive.connection.execute('SELECT * FROM messages WHERE group_id=? AND timestamp>=? AND timestamp<=? LIMIT 201',(item['group_id'],start,end)).fetchall()
        if len(rows)>200:return []
        by_seq={};duplicates=set()
        for candidate in map(archive._row_to_message,rows):
            number=candidate.content_json.get('message_sequence')
            if type(number) is not int or number<=0:continue
            if number in by_seq:duplicates.add(number)
            else:by_seq[number]=candidate
        if seq in duplicates:return []
        session={seq:anchor}
        for direction in (-1,1):
            for offset in range(1,21):
                number=seq+direction*offset;candidate=by_seq.get(number)
                if candidate is None or number in duplicates or candidate.sender_qq!=anchor.sender_qq or candidate.message_type=='forward':break
                if anchor.content_json.get('sender_uid') and candidate.content_json.get('sender_uid')!=anchor.content_json['sender_uid']:break
                session[number]=candidate
        posts=archive.connection.execute("SELECT msg_id,item_key FROM market_posts WHERE group_id=? AND path='0' AND published_at>=? AND published_at<=?",(item['group_id'],start,end)).fetchall()
        products={}
        for post in posts:products.setdefault(post['msg_id'],set()).add(post['item_key'])
        if len(products.get(anchor.msg_id,()))>1:return []
        # Other substantial text is also a possible item boundary, even before
        # extraction has accepted it. Equal-distance pictures stay unassigned.
        boundaries={number for number,candidate in session.items() if candidate.msg_id in products or len(candidate.text.replace('[图片]','').strip())>=5}
        boundaries.add(seq);sources=[]
        for number,candidate in sorted(session.items()):
            if number==seq:continue
            distance=min(abs(number-other) for other in boundaries)
            nearest=[other for other in boundaries if abs(number-other)==distance]
            if nearest!=[seq]:continue
            units=source_units([candidate],service.blocked_ids())
            unit=next((unit for unit in units if unit['path']=='0' and not unit.get('attachment_issue')),None)
            if unit:
                sources.extend((candidate,ref) for ref in unit['attachments'] if isinstance(ref,dict) and ref.get('kind')=='image')
        return sources

    def item_images(item,cache,limit):
        identity=(item['group_id'],item['msg_id'])
        if identity not in cache:
            from ..secondhand_evidence import source_units
            row=archive.connection.execute('SELECT m.*,g.enabled FROM messages m JOIN groups g USING(group_id) WHERE m.group_id=? AND m.msg_id=?',identity).fetchone()
            cache[identity]=source_units([archive._row_to_message(row)],service.blocked_ids()) if row and row['enabled'] else []
        unit=next((unit for unit in cache[identity] if not unit.get('attachment_issue') and unit['key']==item.get('source_key') and unit['path']==item.get('path')),None)
        refs=unit['attachments'] if unit else []
        sources=[(identity[1],None,ref,False) for ref in refs if isinstance(ref,dict) and ref.get('kind')=='image']
        if unit:sources.extend((message.msg_id,message.timestamp.isoformat(),ref,True) for message,ref in continuous_image_sources(item))
        item['images']=[]
        for msg_id,timestamp,ref,related in sources:
            if not ref.get('key') or len(str(ref['key']))>150:continue
            query={'group_id':identity[0],'msg_id':msg_id,'key':ref['key']}
            image={'key':str(ref['key']),'name':str(ref.get('name') or '原消息配图')[:200],
                   'url':'/api/secondhand/image?'+urlencode(query),
                   'preview_url':'/api/secondhand/image?'+urlencode({**query,'preview':'true'}) if config and config.ntqq.qq_number>0 else ''}
            if related:image.update(related=True,source_msg_id=msg_id,source_timestamp=timestamp,source_url='/api/secondhand/source?'+urlencode({'group_id':identity[0],'msg_id':msg_id}))
            item['images'].append(image)
            if len(item['images'])>=limit:break
        return item

    def execute(action,mutate=False):
        try:
            if mutate:
                with operations.claim('bookmark_mutation'):return action()
            return action()
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc
        except LookupError as exc:raise HTTPException(404,str(exc)) from exc
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc

    @app.get('/secondhand',dependencies=[Depends(require_login)])
    async def page(request:Request):return templates.TemplateResponse(request,'secondhand.html',{})

    @app.get('/api/secondhand',dependencies=[Depends(require_login)])
    async def listing(date_from:str|None=None,date_to:str|None=None,q:str=Query('',max_length=100),
                      group_id:int|None=Query(None,gt=0,lt=2**63),direction:str='all',status:str='all',
                      min_price:float|None=Query(None,ge=0,le=1e9),max_price:float|None=Query(None,ge=0,le=1e9),
                      favorite:bool=False,page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),grouped:bool=False,lane:str='sale',category:str='all'):
        def read():
            result=service.list_items(date_from=date_from,date_to=date_to,q=q,group_id=group_id,direction=direction,status=status,min_price=min_price,max_price=max_price,favorite=favorite,page=page,page_size=page_size,grouped=grouped,lane=lane,category=category)
            cache={}
            for item in result['items']:item_images(item,cache,6)
            return result
        return execute(read)

    @app.get('/api/secondhand/groups',dependencies=[Depends(require_login)])
    async def groups():return {'groups':service.groups()}

    @app.post('/api/secondhand/dismiss',dependencies=[Depends(require_login)])
    async def dismiss(payload:Dismiss):return execute(lambda:{'keys':service.dismiss_items(payload.item_ids)},True)

    @app.get('/api/secondhand/dismissed',dependencies=[Depends(require_login)])
    async def dismissed():return {'items':service.dismissed()}

    @app.post('/api/secondhand/dismissed/restore',dependencies=[Depends(require_login)])
    async def restore_interest(payload:RestoreInterest):return execute(lambda:(service.restore_items(payload.keys),{'ok':True})[1],True)

    @app.get('/api/secondhand/blocks',dependencies=[Depends(require_login)])
    async def blocks():return {'items':service.blocked()}

    @app.post('/api/secondhand/blocks',dependencies=[Depends(require_login)])
    async def block(payload:Block):return execute(lambda:(service.block(payload.qq),{'ok':True})[1],True)

    @app.delete('/api/secondhand/blocks/{qq}',dependencies=[Depends(require_login)])
    async def unblock(qq:int):return execute(lambda:(service.unblock(qq),{'ok':True})[1],True)

    @app.get('/api/secondhand/items/{item_id}',dependencies=[Depends(require_login)])
    async def detail(item_id:str):
        def read():
            result=service.detail(item_id);item_images(result['item'],{},100);return result
        return execute(read)

    @app.get('/api/secondhand/source',dependencies=[Depends(require_login)])
    async def source(group_id:int,msg_id:str=Query(max_length=200),direction:str='around',cursor:str|None=None):
        if not archive.connection.execute('SELECT 1 FROM market_groups WHERE group_id=?',(group_id,)).fetchone():raise HTTPException(404,'来源不在二手群范围')
        row=archive.connection.execute('SELECT sender_qq FROM messages WHERE group_id=? AND msg_id=?',(group_id,msg_id)).fetchone()
        if row and row['sender_qq'] in service.blocked_ids():raise HTTPException(404,'此来源已屏蔽')
        from ..message_context import message_context
        data=execute(lambda:message_context(archive,group_id,msg_id,direction=direction,cursor=cursor))
        blocked=service.blocked_ids()
        data['messages']=[message for message in data['messages'] if message['sender_qq'] not in blocked]
        return data

    @app.get('/api/secondhand/attachments',dependencies=[Depends(require_login)])
    async def attachments(group_id:int,msg_id:str=Query(max_length=200)):
        row=archive.connection.execute('SELECT m.content_json,m.sender_qq,g.read_images,g.enabled FROM messages m JOIN market_groups s USING(group_id) JOIN groups g USING(group_id) WHERE m.group_id=? AND m.msg_id=?',(group_id,msg_id)).fetchone()
        if row is None or row['sender_qq'] in service.blocked_ids():raise HTTPException(404,'来源不存在或已屏蔽')
        refs=source_images(group_id,msg_id);cache={r['attachment_key']:dict(r) for r in archive.connection.execute('SELECT * FROM message_attachments WHERE group_id=? AND msg_id=?',(group_id,msg_id))}
        items=[]
        for ref in refs:
            if not isinstance(ref,dict) or ref.get('kind')!='image':continue
            from ..attachments import VERSION
            current=cache.get(ref.get('key'),{})
            if current.get('source_md5')!=str(ref.get('md5','')).lower() or current.get('version')!=VERSION:current={}
            items.append({'key':ref.get('key'),'name':ref.get('name','图片'),'status':current.get('status','pending') if row['read_images'] else 'disabled','text':current.get('text','') if row['read_images'] else '',
                          'url':'/api/secondhand/image?'+urlencode({'group_id':group_id,'msg_id':msg_id,'key':ref.get('key')}) if row['enabled'] else ''})
        return {'items':items,'source_incomplete':bool(json.loads(row['content_json']).get('attachment_source_unavailable'))}

    @app.get('/api/secondhand/image',dependencies=[Depends(require_login)])
    async def image(group_id:int,msg_id:str=Query(max_length=200),key:str=Query(max_length=150),preview:bool=False):
        row=archive.connection.execute('SELECT m.content_json,m.sender_qq,g.read_images,g.enabled FROM messages m JOIN market_groups s USING(group_id) JOIN groups g USING(group_id) WHERE m.group_id=? AND m.msg_id=?',(group_id,msg_id)).fetchone()
        if row is None or row['sender_qq'] in service.blocked_ids():raise HTTPException(404,'来源不存在或已屏蔽')
        if not row['enabled']:raise HTTPException(403,'这个群已停用')
        ref=next((ref for ref in source_images(group_id,msg_id) if ref.get('key')==key),None)
        if ref is None:raise HTTPException(404,'图片来源不存在')
        from ..attachments import AttachmentService
        reader=AttachmentService(archive,config)
        try:data,mime=await guarded_to_thread(reader.preview_image if preview else reader.original_image,ref)
        except (OSError,ValueError,NotImplementedError) as exc:raise HTTPException(404,'图片未下载或无法核实来源') from exc
        return Response(data,media_type=mime,headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @app.patch('/api/secondhand/items/{item_id}',dependencies=[Depends(require_login)])
    async def favorite(item_id:str,payload:Favorite):return execute(lambda:(service.set_favorite(item_id,payload.favorite),{'ok':True})[1],True)

    @app.get('/api/secondhand/unavailable',dependencies=[Depends(require_login)])
    async def unavailable():
        rows=archive.connection.execute('''SELECT s.*,g.name AS group_name,m.text,m.timestamp FROM market_scans s JOIN groups g USING(group_id)
            JOIN messages m ON m.group_id=s.group_id AND m.msg_id=s.msg_id
            WHERE s.state!='done' AND NOT EXISTS(SELECT 1 FROM market_blocks b WHERE b.qq=m.sender_qq)
            ORDER BY s.updated_at DESC LIMIT 100''').fetchall()
        return {'items':[dict(row) for row in rows]}

    @app.get('/api/secondhand/progress',dependencies=[Depends(require_login)])
    async def status():return dict(progress)

    @app.post('/api/secondhand/refresh',dependencies=[Depends(require_login)])
    async def refresh(payload:Refresh):
        try:date_window(payload.date_from,payload.date_to,timezone_name)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if config is None:raise HTTPException(503,'本地配置不可用')
        if not refresh_lock.acquire(False):raise HTTPException(409,'二手信息正在更新')
        def worker():
            from ..secondhand_runner import refresh as run
            database=archive.connection.execute('PRAGMA database_list').fetchone()['file']
            with operations.claim('collect'):
                worker_archive=Archive.open(Path(database)) if database else archive
                try:
                    progress.update(running=True,message='正在读取本地二手群…',result=None)
                    start,end=recent_window(timezone_name)
                    result=run(config,worker_archive,payload.date_from or start,payload.date_to or end,on_progress=lambda message:progress.update(message=message),force=payload.rebuild)
                    progress.update(result=result,message='本次更新已结束')
                    return result
                finally:
                    progress['running']=False
                    if worker_archive is not archive:worker_archive.close()
        try:return await guarded_to_thread(worker)
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except Exception as exc:raise HTTPException(503,'二手信息更新失败，请检查本地源库和 AI 连接') from exc
        finally:refresh_lock.release()
