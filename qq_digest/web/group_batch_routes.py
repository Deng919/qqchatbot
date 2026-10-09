"""Strict authenticated batch APIs using a single frozen selected group scope."""
from datetime import date, datetime, time
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from ..attachments import AttachmentService, allowed, policy
from ..group_batch import batch_totals, collect_batch, require_existing_groups
from ..group_selection import normalize_group_selection
from ..models import GroupConfig
from ..operations import OperationBusy
from .attachment_routes import attachment_listing, attachment_messages
from .worker_threads import guarded_to_thread


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class GroupSelectionRequest(StrictRequest):
    group_ids: list[StrictInt]

    @field_validator('group_ids')
    @classmethod
    def valid_groups(cls, value):
        return list(normalize_group_selection(group_ids=value, required=True))


class CollectBatchRequest(GroupSelectionRequest):
    start: StrictStr
    end: StrictStr
    refresh: StrictBool = True
    dry_run: StrictBool = False

    @field_validator('start','end')
    @classmethod
    def exact_date(cls, value):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            raise ValueError('日期必须是 YYYY-MM-DD')
        date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def valid_range(self):
        if self.start > self.end: raise ValueError('开始日期不能晚于结束日期')
        if self.dry_run and (date.fromisoformat(self.end)-date.fromisoformat(self.start)).days > 90:
            raise ValueError('单次缺口检查最多支持 91 天')
        return self


class SettingsPatch(StrictRequest):
    enabled: StrictBool | None = None
    daily_summary: StrictBool | None = None
    important_candidates: StrictBool | None = None
    read_images: StrictBool | None = None
    read_files: StrictBool | None = None
    category: Literal['tech','resource','project','study','general'] | None = None
    collection_window_days: StrictInt | None = Field(default=None, ge=1, le=365)
    keywords: list[StrictStr] | None = None

    @field_validator('keywords')
    @classmethod
    def valid_keywords(cls, value):
        if value is not None and (len(value)>20 or any(not k.strip() or len(k.strip())>50 for k in value)):
            raise ValueError('keywords 必须是最多 20 个非空字符串')
        return list(dict.fromkeys(k.strip() for k in value)) if value is not None else None

    @model_validator(mode='after')
    def explicit_nonempty(self):
        if not self.model_fields_set or any(getattr(self,k) is None for k in self.model_fields_set):
            raise ValueError('请选择有效设置字段，不允许空值')
        return self


class SettingsBatchRequest(GroupSelectionRequest):
    patch: SettingsPatch


class AddedGroup(StrictRequest):
    group_id: StrictInt
    name: StrictStr

    @field_validator('group_id')
    @classmethod
    def valid_group(cls,value):
        normalize_group_selection(group_id=value, required=True)
        return value


class AddBatchRequest(StrictRequest):
    groups: list[AddedGroup] = Field(min_length=1,max_length=1000)

    @field_validator('groups')
    @classmethod
    def valid_groups(cls, value):
        ids = normalize_group_selection(group_ids=[g.group_id for g in value],required=True)
        unique = {g.group_id:g for g in reversed(value)}
        return [unique[gid] for gid in ids]


def add_group_batch_routes(app, *, archive, config, require_login, operations):
    def preflight(ar, ids):
        try: return require_existing_groups(ar,ids)
        except KeyError as exc: raise HTTPException(404,str(exc)) from exc

    @app.post('/api/collect/batch', dependencies=[Depends(require_login)])
    async def collect(request: Request, payload: CollectBatchRequest):
        try:
            with operations.claim('collect'):
                ar = archive(request); ids = preflight(ar,payload.group_ids)
                cfg = config(request)
                if not cfg or not cfg.ntqq.enabled or not cfg.ntqq.db_dir:
                    raise HTTPException(400,'请先启用 NTQQ 并配置源数据库目录')
                tz = ZoneInfo(cfg.summary.timezone)
                start = datetime.combine(date.fromisoformat(payload.start),time.min,tz)
                end = datetime.combine(date.fromisoformat(payload.end),time.max,tz)
                return await guarded_to_thread(collect_batch, config=cfg,group_ids=ids,start=start,end=end,
                    refresh=payload.refresh,dry_run=payload.dry_run)
        except OperationBusy as exc: raise HTTPException(409,str(exc)) from exc

    @app.post('/api/groups/batch/add', dependencies=[Depends(require_login)])
    async def add(request: Request, payload: AddBatchRequest):
        try:
            with operations.claim('group_mutation'):
                ar = archive(request)
                existing = {g.group_id for g in ar.all_groups()}
                ar.upsert_groups([GroupConfig(group_id=g.group_id,name=g.name) for g in payload.groups if g.group_id not in existing])
                results = [{'group_id':g.group_id,'status':'skipped' if g.group_id in existing else 'success'} for g in payload.groups]
                return {'results':results,'totals':{'added':sum(r['status']=='success' for r in results),
                    'skipped':sum(r['status']=='skipped' for r in results)}}
        except OperationBusy as exc: raise HTTPException(409,str(exc)) from exc

    @app.post('/api/groups/batch/settings', dependencies=[Depends(require_login)])
    async def settings(request: Request, payload: SettingsBatchRequest):
        try:
            with operations.claim('group_mutation'):
                ar = archive(request); ids = preflight(ar,payload.group_ids)
                patch = payload.patch.model_dump(exclude_unset=True)
                values = [json.dumps(v,ensure_ascii=False) if k=='keywords' else int(v) if isinstance(v,bool) else v for k,v in patch.items()]
                with ar.transaction():
                    ar.connection.executemany(f"UPDATE groups SET {','.join(k+'=?' for k in patch)} WHERE group_id=?",
                        [(*values,gid) for gid in ids])
                results = [{'group_id':gid,'status':'success'} for gid in ids]
                return {'results':results,'totals':batch_totals(results)}
        except OperationBusy as exc: raise HTTPException(409,str(exc)) from exc

    @app.post('/api/groups/batch/attachments/read', dependencies=[Depends(require_login)])
    async def read(request: Request, payload: GroupSelectionRequest):
        try:
            with operations.claim('collect'):
                ar = archive(request); ids = preflight(ar,payload.group_ids); cfg = config(request)
                def worker():
                    results = []
                    source = []
                    for gid in ids:
                        group = policy(ar,gid)
                        if not (allowed(group,'image') or allowed(group,'file')):
                            results.append({'group_id':gid,'status':'failed','error':'请先保存并开启这个群的附件读取'})
                        else:
                            source.extend(ar._row_to_message(row) for row in attachment_messages(ar,gid))
                            results.append({'group_id':gid,'status':'success'})
                    # One prepare call preserves the existing total 20 read limit,
                    # verified downloaded-file requirement and shared AI budget.
                    AttachmentService(ar,cfg).prepare(source,retry=True)
                    for result in results:
                        if result['status']=='success': result.update(attachment_listing(ar,result['group_id']))
                    return {'results':results,'totals':batch_totals(results)}
                return await guarded_to_thread(worker)
        except OperationBusy as exc: raise HTTPException(409,str(exc)) from exc
