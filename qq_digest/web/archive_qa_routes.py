"""Scoped archive questions with explicit preview and bounded ephemeral tickets."""
import asyncio
import hashlib
from datetime import date, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..archive_qa import ArchiveQAService
from ..ai.client import AIClient, AIError
from ..config import ConfigError
from ..distribution import is_public_distribution, validate_public_ai
from ..operations import OperationBusy


class PreviewQuestion(BaseModel):
    model_config={'extra':'forbid'}
    question: str = Field(min_length=2,max_length=2000)
    query: str = Field(default='',max_length=100)
    group_id: int | None = Field(default=None,ge=-(2**63),lt=2**63)
    date_from: date
    date_to: date


class AskQuestion(BaseModel):
    model_config={'extra':'forbid'}
    ticket: str = Field(min_length=20,max_length=100)


def add_archive_qa_routes(app, *, archive, config, templates, require_login, operations):
    service=ArchiveQAService(archive,config.summary.timezone if config else 'Asia/Shanghai',
        max_chars=min(config.ai.max_context_chars,16000) if config else 16000)
    app.state.archive_qa=service

    def owner(request):
        return hashlib.sha256(request.cookies.get('qq_digest_session','').encode()).hexdigest()

    async def execute(action):
        def worker():
            with operations.claim('archive_qa'):return action()
        try:return await asyncio.to_thread(worker)
        except LookupError as exc:raise HTTPException(410,str(exc)) from exc
        except OperationBusy as exc:raise HTTPException(409,str(exc)) from exc
        except (AIError,ConfigError,OSError) as exc:
            raise HTTPException(503,'问答暂不可用，请检查 DeepSeek API Key 和网络后重试') from exc
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        except OverflowError as exc:raise HTTPException(422,'日期超出有效范围') from exc

    @app.get('/archive-qa')
    async def qa_page(request:Request):
        try:require_login(request)
        except HTTPException:return RedirectResponse('/login',303)
        today=datetime.now(service.zone).date()
        return templates.TemplateResponse(request=request,name='archive_qa.html',context={
            'qa_timezone':service.zone.key,'qa_date_to':today.isoformat(),
            'qa_date_from':(today-timedelta(days=6)).isoformat()})

    @app.post('/api/archive-qa/preview',dependencies=[Depends(require_login)])
    async def preview(request:Request,payload:PreviewQuestion):
        return await execute(lambda:service.preview(owner=owner(request),question=payload.question,
            query=payload.query,group_id=payload.group_id,date_from=payload.date_from.isoformat(),date_to=payload.date_to.isoformat()))

    @app.post('/api/archive-qa/ask',dependencies=[Depends(require_login)])
    async def ask(request:Request,payload:AskQuestion):
        class LazyClient:
            client=None
            def chat(self,messages):
                cfg=request.app.state.config
                if cfg is None:raise ConfigError('AI 配置不可用')
                base=cfg.resolve_base_url()
                if is_public_distribution():validate_public_ai(['compatible'],base)
                self.client=AIClient(base_url=base,api_key=cfg.resolve_api_key(),model=cfg.ai.model,
                    json_mode=True,timeout_seconds=cfg.ai.timeout_seconds,max_retries=2)
                return self.client.chat(messages)
            def close(self):
                if self.client:self.client.close()
        def answer():
            client=LazyClient()
            try:return service.ask(payload.ticket,owner(request),client)
            finally:client.close()
        return await execute(answer)

    @app.get('/api/archive-qa/sources/{ticket}/{source_id}',dependencies=[Depends(require_login)])
    async def source(request:Request,ticket:str,source_id:str):
        return await execute(lambda:service.source(ticket,owner(request),source_id))
