"""Versioned single Cockpit connection, with atomic protected-key replacement."""
from pathlib import Path
import hashlib
import os
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..ai.client import AIError, AILimitExceeded
from ..config import AIConfig
from ..distribution import validate_ai_base_url
from ..first_use import SetupConflict
from ..operations import OperationBusy
from ..ai.key_store import save_ui_api_key, ui_api_key_exists
from .worker_threads import guarded_to_thread


class VersionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')


class ConnectionPayload(VersionPayload):
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    json_mode: StrictBool = False
    api_key: str = Field(default='', max_length=4096, repr=False)


def add_ai_connection_routes(app, *, require_login, test_connection):
    def service():
        svc = getattr(app.state, 'first_use', None)
        if svc is None:
            raise HTTPException(503, 'AI 配置不可用')
        return svc

    def snapshot(svc):
        cfg = svc.config
        ai = cfg.ai
        return dict(available=bool(svc.store.path), revision=svc.store.revision,
            base_url=ai.base_url, model=ai.model, json_mode=ai.json_mode,
            key_configured=bool((ai.ui_api_key_file and ui_api_key_exists(Path(ai.ui_api_key_file)))
                or (ai.api_key_env and os.environ.get(ai.api_key_env))
                or (ai.api_key_file and (Path(ai.api_key_file) if Path(ai.api_key_file).is_absolute()
                    else cfg.data_dir / ai.api_key_file).is_file())))

    async def execute(expected, action):
        def worker():
            svc = service()
            with app.state.operations.claim('setup_mutation'), svc.store.lock:
                svc.store.check(expected)
                return action(svc)
        try:
            return await guarded_to_thread(worker)
        except (SetupConflict, OperationBusy) as exc:
            raise HTTPException(409, str(exc)) from exc
        except AILimitExceeded as exc:
            raise HTTPException(429, str(exc)) from exc
        except AIError as exc:
            raise HTTPException(503, 'Cockpit 连接测试失败，请检查地址、模型、密钥和网络') from exc
        except ValueError as exc:
            # Validation errors can include the supplied secret. Never echo them.
            raise HTTPException(422, 'AI 设置无效，请检查地址、模型和密钥') from exc
        except OSError as exc:
            raise HTTPException(503, '保存失败，请检查配置目录权限和存储空间') from exc

    @app.get('/api/ai-settings')
    async def read(request: Request):
        require_login(request)
        svc = service()
        def worker():
            with svc.store.lock:
                return snapshot(svc)
        return await guarded_to_thread(worker)

    @app.put('/api/ai-settings')
    async def save(request: Request, payload: ConnectionPayload):
        require_login(request)
        def action(svc):
            cfg = svc.config
            values = payload.model_dump(exclude={'api_key', 'expected_revision'})
            values['base_url'] = values['base_url'].strip()
            values['model'] = values['model'].strip()
            validate_ai_base_url(values['base_url'])
            AIConfig.model_validate({**cfg.ai.model_dump(), **values})
            created = None
            try:
                if payload.api_key:
                    created = cfg.data_dir / 'config' / 'secrets' / f'ai-key-{uuid4().hex}.txt'
                    save_ui_api_key(created, payload.api_key)
                    values['ui_api_key_file'] = str(created)
                content = svc.store.save(payload.expected_revision, {'ai': values}, svc._state())
                if hasattr(app.state, 'desktop_config_signature'):
                    app.state.desktop_config_signature = hashlib.sha256(
                        str(svc.store.path).casefold().encode('utf-8') + b'\0' + content).hexdigest()
            except BaseException:
                if created and cfg.ai.ui_api_key_file != str(created):
                    try: created.unlink(missing_ok=True)
                    except OSError: pass
                raise
            return dict(ok=True, **snapshot(svc))
        return await execute(payload.expected_revision, action)

    @app.post('/api/ai-settings/test')
    async def test(request: Request, payload: VersionPayload):
        require_login(request)
        def action(svc):
            test_connection(svc.config.model_copy(deep=True))
            return dict(ok=True)
        return await execute(payload.expected_revision, action)
