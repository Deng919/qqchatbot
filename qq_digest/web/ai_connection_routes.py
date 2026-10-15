"""Versioned AI connection settings; reuse the setup config writer and operation lease."""
from pathlib import Path
import hashlib
import re
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..ai.client import AIError, AILimitExceeded
from ..config import AIConfig, ConfigError
from ..distribution import is_public_distribution
from ..first_use import SetupConflict
from ..operations import OperationBusy
from ..storage_files import safe_path
from ..ai.key_store import save_ui_api_key, ui_api_key_exists
from .worker_threads import guarded_to_thread


class VersionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')


class ConnectionPayload(VersionPayload):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,50}$')
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    supports_vision: StrictBool = False
    json_mode: StrictBool = True
    api_key: str = Field(default='', max_length=4096, repr=False)


class PurposePayload(VersionPayload):
    purpose_connections: dict[str, str]


class TestPayload(VersionPayload):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,50}$')


def add_ai_connection_routes(app, *, require_login, test_connection):
    def service():
        svc = getattr(app.state, 'first_use', None)
        if svc is None:
            raise HTTPException(503, 'AI 配置不可用')
        return svc

    def snapshot(svc):
        import os
        cfg = svc.config
        return dict(available=bool(svc.store.path) and not is_public_distribution(),
            revision=svc.store.revision,
            connections=[dict(id=c.id,name=c.name,base_url=c.base_url,model=c.model,
                supports_vision=c.supports_vision,json_mode=c.json_mode,
                key_configured=bool((c.ui_api_key_file and ui_api_key_exists(Path(c.ui_api_key_file)))
                    or (c.api_key_env and os.environ.get(c.api_key_env))
                    or (c.api_key_file and Path(c.api_key_file).is_file()))) for c in cfg.ai.connections],
            purpose_connections={p:cfg.ai.purpose_connections.get(p,'') for p in ('summary','report_qa','archive_qa','vision')})

    def persist(svc, values, revision):
        # Validate the full AI section before writing a key or configuration.
        AIConfig.model_validate({**svc.config.ai.model_dump(), **values})
        content=svc.store.save(revision, {'ai':values}, svc._state())
        if hasattr(app.state, 'desktop_config_signature'):
            # Same identity as desktop.config_signature, using committed bytes.
            # Re-reading here could fail after commit and discard an active key.
            app.state.desktop_config_signature = hashlib.sha256(
                str(svc.store.path).casefold().encode('utf-8')+b'\0'+content).hexdigest()

    def cleanup(svc, connection):
        """Only remove unreferenced key files created by this UI; never external keys."""
        if not connection or not connection.ui_api_key_file:
            return False
        path = Path(connection.ui_api_key_file)
        cfg = svc.config
        references = [cfg.ai.ui_api_key_file, cfg.ai.api_key_file, cfg.security.session_secret_file]
        references += [value for c in cfg.ai.connections for value in (c.ui_api_key_file,c.api_key_file)]
        try:
            referenced_paths=[Path(value).expanduser() for value in references if value]
            if any((value if value.is_absolute() else cfg.data_dir/value).absolute() == path.absolute()
                   for value in referenced_paths):
                return False
            root = cfg.data_dir/'config'/'secrets'
            name_pattern=r'ai-'+re.escape(connection.id)+r'(?:-[0-9a-f]{32})?\.txt'
            if path.parent.absolute() != root.absolute() or not re.fullmatch(name_pattern,path.name):
                return False
            safe_path(path, root)
            path.unlink(missing_ok=True)
            return False
        except (OSError, ValueError):
            return True

    async def execute(expected, action):
        def worker():
            svc=service()
            if is_public_distribution():
                raise HTTPException(403,'公开版不支持自定义 AI 连接')
            with app.state.operations.claim('setup_mutation'), svc.store.lock:
                svc.store.check(expected)
                return action(svc)
        try:
            return await guarded_to_thread(worker)
        except (SetupConflict, OperationBusy) as exc:
            raise HTTPException(409,str(exc)) from exc
        except AILimitExceeded as exc:
            raise HTTPException(429,str(exc)) from exc
        except AIError as exc:
            raise HTTPException(503,'AI 连接测试失败，请检查地址、模型、密钥和网络') from exc
        except ValueError as exc:
            # Pydantic exception text may contain secrets in input_value.
            raise HTTPException(422,'AI 设置无效，请检查连接、用途和图片支持') from exc
        except OSError as exc:
            raise HTTPException(503,'保存失败，请检查配置目录权限和存储空间') from exc

    @app.get('/api/ai-settings/connections')
    async def read(request:Request):
        require_login(request)
        svc=service()
        def worker():
            with svc.store.lock:
                return snapshot(svc)
        return await guarded_to_thread(worker)

    @app.put('/api/ai-settings/connections')
    async def save(request:Request,payload:ConnectionPayload):
        require_login(request)
        def action(svc):
            cfg=svc.config
            existing=next((c for c in cfg.ai.connections if c.id==payload.id),None)
            connection=existing.model_dump() if existing else dict(id=payload.id,provider='compatible')
            connection.update(payload.model_dump(exclude={'api_key','expected_revision'}))
            created=None
            if payload.api_key:
                # Versioned immutable key first, atomic config pointer second. A crash
                # can leave an orphan, but never a config pointing to an uninstalled key.
                created=safe_path(cfg.data_dir/'config'/'secrets'/f'ai-{payload.id}-{uuid4().hex}.txt',cfg.data_dir)
                connection['ui_api_key_file']=str(created)
            connections=[connection if c.id==payload.id else c.model_dump() for c in cfg.ai.connections]
            if existing is None: connections.append(connection)
            AIConfig.model_validate({**cfg.ai.model_dump(),'connections':connections})
            try:
                if created: save_ui_api_key(created,payload.api_key)
                persist(svc,{'connections':connections},payload.expected_revision)
            except BaseException:
                if created and not any(c.ui_api_key_file==str(created) for c in cfg.ai.connections):
                    try: created.unlink(missing_ok=True)
                    except OSError: pass
                raise
            pending=cleanup(svc,existing)
            return dict(ok=True,**snapshot(svc),key_cleanup_pending=pending)
        return await execute(payload.expected_revision,action)

    @app.put('/api/ai-settings/purposes')
    async def purposes(request:Request,payload:PurposePayload):
        require_login(request)
        def action(svc):
            allowed={'summary','report_qa','archive_qa','vision'}
            if set(payload.purpose_connections)-allowed: raise ValueError('unknown purpose')
            choices={p:c for p,c in payload.purpose_connections.items() if c}
            persist(svc,{'purpose_connections':choices},payload.expected_revision)
            return dict(ok=True,**snapshot(svc))
        return await execute(payload.expected_revision,action)

    @app.delete('/api/ai-settings/connections/{connection_id}')
    async def delete(request:Request,connection_id:str,expected_revision:str=Query(pattern=r'^[0-9a-f]{64}$')):
        require_login(request)
        def action(svc):
            removed=next((c for c in svc.config.ai.connections if c.id==connection_id),None)
            if removed is None: raise HTTPException(404,'AI 连接不存在')
            if connection_id in svc.config.ai.purpose_connections.values(): raise HTTPException(409,'请先取消该连接的用途选择')
            persist(svc,{'connections':[c.model_dump() for c in svc.config.ai.connections if c.id!=connection_id]},expected_revision)
            return dict(ok=True,**snapshot(svc),key_cleanup_pending=cleanup(svc,removed))
        return await execute(expected_revision,action)

    @app.post('/api/ai-settings/connections/test')
    async def test(request:Request,payload:TestPayload):
        require_login(request)
        def action(svc):
            if not any(c.id==payload.id for c in svc.config.ai.connections):raise HTTPException(404,'AI 连接不存在')
            test_connection(svc.config.model_copy(deep=True),payload.id)
            return dict(ok=True)
        return await execute(payload.expected_revision,action)
