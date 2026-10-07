"""Read all verified references for one report claim and its optional context."""
from __future__ import annotations

from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field
from starlette.requests import Request

from ..message_context import message_context
from ..report_sources import load_verified_report_sources
from ..report_revisions import ReportRevisionService,RevisionConflict
from ..sender_identity import sender_fields


class RelatedMessagesPayload(BaseModel):
    source_ids:list[str]=Field(min_length=1)
    expected_version:int|None=Field(default=None,ge=0,strict=True)
    anchor_id:str|None=None
    direction:Literal['around','before','after']='around'
    cursor:str|None=None


def add_report_related_routes(app,*,archive,config,require_login):
    @app.post('/api/reports/{report_kind}/{report_id}/related-messages',dependencies=[Depends(require_login)])
    async def related(request:Request,report_kind:str,report_id:int,payload:RelatedMessagesPayload):
        ar=archive(request);revisions=ReportRevisionService(ar)
        try:
            row=revisions.report(report_kind,report_id)
            current=revisions.current_version(report_kind,report_id)
            if payload.expected_version is not None:revisions.check_version(report_kind,report_id,payload.expected_version)
            document=revisions.version(report_kind,report_id,current)['payload'] if current else row['json_path']
        except RevisionConflict as exc:raise HTTPException(409,detail=str(exc)) from exc
        except (LookupError,ValueError) as exc:raise HTTPException(404,detail=str(exc)) from exc
        start=row['report_date'] if report_kind=='daily' else row['start_date']
        end=row['report_date'] if report_kind=='daily' else row['end_date']
        cfg=config(request)
        claims=load_verified_report_sources(ar.connection,document,group_id=row['group_id'],start_date=start,end_date=end,
            timezone_name=cfg.summary.timezone if cfg else 'Asia/Shanghai')
        requested=set(payload.source_ids)
        claim=next((claim for claim in claims or [] if requested==set(claim['source_ids'])),None)
        if not requested or claim is None:raise HTTPException(404,detail='关联消息已变化，请重新打开摘要')
        ids=claim['source_ids']
        if payload.anchor_id is not None:
            if payload.anchor_id not in requested:raise HTTPException(404,detail='这条消息不属于当前关联')
            try:data=message_context(ar,row['group_id'],payload.anchor_id,direction=payload.direction,cursor=payload.cursor)
            except LookupError as exc:raise HTTPException(404,detail=str(exc)) from exc
            except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
        else:
            rows=[]
            for offset in range(0,len(ids),500):
                batch=ids[offset:offset+500];marks=','.join('?' for _ in batch)
                rows.extend(ar.connection.execute(f'SELECT msg_id,timestamp,sender_qq,content_json,text FROM messages WHERE group_id=? AND msg_id IN ({marks})',
                    (row['group_id'],*batch)).fetchall())
            rows.sort(key=lambda item:(item['timestamp'],item['msg_id']))
            if len(rows)!=len(ids):raise HTTPException(409,detail='关联消息已变化，请重新打开摘要')
            data={'messages':[{**{key:item[key] for key in item.keys() if key!='content_json'},**sender_fields(item)} for item in rows]}
        try:revisions.check_version(report_kind,report_id,current)
        except RevisionConflict as exc:raise HTTPException(409,detail=str(exc)) from exc
        data.update(related_ids=ids,group_id=row['group_id'],current_version=current)
        return data
