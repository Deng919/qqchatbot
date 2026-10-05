"""Core knowledge access is independent of the optional candidate review UI."""
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Request, Path
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from ..knowledge_library import KnowledgeLibrary
from ..message_context import message_context
from .operations import OperationBusy

GroupId = Annotated[int, Field(ge=-(2**63),lt=2**63,strict=True)]
ItemId = Annotated[int, Path(ge=1,lt=2**63)]


class SaveKnowledge(BaseModel):
    model_config={'extra':'forbid'}
    group_id: GroupId
    msg_id: str = Field(min_length=1,max_length=512)
    item_type: Literal['resource','experience']
    title: str = Field(min_length=1,max_length=200)
    reason: str = Field(default='',max_length=2000)
    link: str = Field(default='',max_length=2048)

    @field_validator('title')
    @classmethod
    def meaningful_title(cls,value):
        value=value.strip()
        if not value: raise ValueError('请填写标题')
        return value

    @field_validator('link')
    @classmethod
    def safe_link(cls,value):
        value=value.strip()
        if value and not value.startswith(('https://','http://')):
            raise ValueError('链接需以 https:// 或 http:// 开头')
        return value


def add_knowledge_routes(app, *, archive, knowledge, config, templates, require_login, operations):
    library=KnowledgeLibrary(archive,knowledge,config.summary.timezone if config else 'Asia/Shanghai')
    app.state.knowledge_library=library

    def execute(action):
        try: return action()
        except (LookupError,KeyError) as exc: raise HTTPException(404,str(exc)) from exc
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        except OperationBusy as exc: raise HTTPException(409,'后台任务正在运行，请稍后重试保存') from exc
        except OSError as exc: raise HTTPException(503,'知识保存失败，请检查存储位置后重试；原数据已保留') from exc

    @app.get('/knowledge')
    async def knowledge_page(request:Request):
        try: require_login(request)
        except HTTPException: return RedirectResponse('/login',status_code=303)
        return templates.TemplateResponse(request,'knowledge.html',{})

    @app.get('/api/knowledge',dependencies=[Depends(require_login)])
    async def list_knowledge(q:str=Query('',max_length=100),group_id:int|None=Query(None,ge=-(2**63),lt=2**63),
                             item_type:Literal['resource','experience']|None=None,page:int=Query(1,ge=1,le=2**31)):
        return library.list_items(q=q,group_id=group_id,item_type=item_type,page=page)

    @app.get('/api/knowledge/from-message',dependencies=[Depends(require_login)])
    async def source_preview(group_id:int=Query(ge=-(2**63),lt=2**63),msg_id:str=Query(min_length=1,max_length=512)):
        return execute(lambda:library.source(group_id,msg_id))

    @app.post('/api/knowledge/from-message',dependencies=[Depends(require_login)])
    async def save_source(payload:SaveKnowledge):
        def save():
            with operations.claim('knowledge_mutation'):
                return library.save_message(**payload.model_dump())
        return execute(save)

    @app.get('/api/knowledge/{candidate_id}',dependencies=[Depends(require_login)])
    async def knowledge_detail(candidate_id:ItemId):
        return execute(lambda:library.detail(candidate_id))

    @app.get('/api/knowledge/{candidate_id}/sources/{msg_id}',dependencies=[Depends(require_login)])
    async def knowledge_source(candidate_id:ItemId,msg_id:str,direction:str='around',cursor:str|None=None):
        def source():
            item=library.detail(candidate_id)
            if msg_id not in item['message_ids']: raise LookupError('知识未关联这条原消息')
            return message_context(archive,item['group_id'],msg_id,direction=direction,cursor=cursor)
        return execute(source)

    return library
