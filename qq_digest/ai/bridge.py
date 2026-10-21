from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .client import AIError, AILimitExceeded


from .protocol import ChatProvider


def _decode_process_output(value: str | bytes | None) -> str:
    """Decode bridge output using the two encodings emitted by the wrapper."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return value.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise AIError("chatgpt-bridge 返回文本编码无效") from exc


def _parse_json_content(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        end = next(
            (index for index, line in enumerate(lines[1:], 1) if line.strip().startswith("```")),
            len(lines),
        )
        text = "\n".join(lines[1:end])
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise AIError("GPT 返回的不是有效 JSON")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIError("GPT 返回的不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise AIError("GPT 返回的 JSON 顶层必须是对象")
    return value


class BridgeAIClient:
    def __init__(
        self,
        *,
        wrapper_path: Path,
        account_directory: Path,
        model: str = "",
        timeout_seconds: float = 180,
        queue_timeout_seconds: int = 60,
        runner: Callable[..., subprocess.CompletedProcess[str | bytes]] = subprocess.run,
        request_directory: Path | None = None,
    ) -> None:
        self.wrapper_path = Path(wrapper_path)
        self.account_directory = Path(account_directory)
        self.model = model.strip()
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.queue_timeout_seconds = max(1, queue_timeout_seconds)
        self._runner = runner
        self.request_directory = Path(request_directory) if request_directory else None

    def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if any(isinstance(message.get('content'),list) for message in messages):
            return self._chat_images(messages)
        request_path: Path | None = None
        try:
            request = {"messages": messages}
            if self.model:
                request["model"] = self.model
            directory = str(self.request_directory) if self.request_directory else None
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix="qq-digest-chat-",
                dir=directory,
                encoding="utf-8",
                delete=False,
            ) as handle:
                json.dump(request, handle, ensure_ascii=False)
                request_path = Path(handle.name)

            command = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.wrapper_path),
                "-RequestPath",
                str(request_path),
                "-AccountDirectory",
                str(self.account_directory),
                "-QueueTimeoutSeconds",
                str(self.queue_timeout_seconds),
            ]
            result = self._runner(
                command,
                check=False,
                capture_output=True,
                text=False,
                timeout=self.timeout_seconds,
                # Pipes capture output but do not prevent a console in a GUI EXE.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                message = "chatgpt-bridge 调用失败"
                try:
                    stderr_text = _decode_process_output(result.stderr)
                    error = json.loads(stderr_text.strip())
                    if isinstance(error, dict) and isinstance(error.get("error"), str):
                        message = error["error"][:1000]
                except (AIError, json.JSONDecodeError, AttributeError):
                    pass
                raise AIError(message)
            stdout_text = _decode_process_output(result.stdout)
            if not stdout_text.strip():
                message = "chatgpt-bridge 未返回有效输出"
                try:
                    stderr_text = _decode_process_output(result.stderr)
                    error = json.loads(stderr_text.strip())
                    if isinstance(error, dict) and isinstance(error.get("error"), str):
                        message = error["error"][:1000]
                except (AIError, json.JSONDecodeError, AttributeError):
                    pass
                raise AIError(message)
            try:
                payload = json.loads(stdout_text)
                content = payload["content"]
                if not payload.get("ok") or not isinstance(content, str):
                    raise ValueError
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise AIError("chatgpt-bridge 返回结构无效") from exc
            return _parse_json_content(content)
        except subprocess.TimeoutExpired as exc:
            raise AIError("chatgpt-bridge 调用超时") from exc
        except OSError as exc:
            raise AIError(f"无法启动 chatgpt-bridge 包装器: {exc}") from exc
        finally:
            if request_path is not None:
                request_path.unlink(missing_ok=True)

    def close(self) -> None:
        return None

    def _chat_images(self, messages) -> dict[str, Any]:
        """Use the installed pool's multimodal entry, retaining its account lock/cleanup."""
        wrapper=self.wrapper_path.with_name('invoke-pool-mcp.ps1')
        local=Path(os.environ.get('LOCALAPPDATA',''))
        cli=local/'gpttoimage/chatgpt-bridge/node_modules/chatgpt-bridge/dist/cli.js'
        node=shutil.which('node')
        if not wrapper.is_file() or not cli.is_file() or not node:
            raise AIError('本地桥接多模态入口不可用，请更新桥接或配置图片 AI 连接')
        prompt=[];system=[];images=[]
        for message in messages:
            text=[];content=message.get('content')
            if isinstance(content,str):text.append(content)
            elif isinstance(content,list):
                for block in content:
                    if block.get('type')=='text':text.append(block.get('text',''))
                    elif block.get('type')=='image_url':
                        url=block.get('image_url',{}).get('url','')
                        if not url.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,','data:image/gif;base64,')):
                            raise AIError('多模态桥接只接收经过核实的内嵌图片')
                        images.append(url)
                    else:raise AIError('多模态桥接不支持此内容格式')
            else:raise AIError('多模态消息格式无效')
            (system if message.get('role') in ('system','developer') else prompt).extend(text)
        arguments={'prompt':'\n\n'.join(prompt),'attachments':images}
        if system:arguments['system']='\n\n'.join(system)
        if self.model:arguments['model']=self.model
        command=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper),
                 '-AccountDirectory',str(self.account_directory),'-NodePath',node,'-BridgeCliPath',str(cli)]
        if self.request_directory:command+=['-RuntimeRoot',str(self.request_directory/'bridge-vision-runtime')]
        try:
            result=self._runner(command,input=json.dumps({'name':'chat','arguments':arguments},ensure_ascii=False).encode('utf-8'),
                check=False,capture_output=True,text=False,timeout=self.timeout_seconds,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise AIError('本地图片识别桥接调用失败')
            payload=json.loads(_decode_process_output(result.stdout))
            if payload.get('isError'):raise AIError('本地图片识别失败，请检查桥接登录和图片 AI 连接')
            content='\n'.join(b['text'] for b in payload['content'] if b.get('type')=='text')
            return _parse_json_content(content)
        except subprocess.TimeoutExpired as exc:raise AIError('本地图片识别超时，请稍后重试') from exc
        except (OSError,ValueError,KeyError,TypeError) as exc:raise AIError('本地图片识别桥接返回无效结果') from exc


class FallbackAIClient:
    def __init__(
        self,
        *,
        primary: ChatProvider,
        fallback: ChatProvider | None = None,
        fallback_factory: Callable[[], ChatProvider] | None = None,
        primary_name: str = "chatgpt_bridge",
        fallback_name: str = "compatible",
    ) -> None:
        self.primary = primary
        self._fallback = fallback
        self._fallback_factory = fallback_factory
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.last_provider = ""
        self.last_fallback_reason = ""

    @property
    def fallback(self) -> ChatProvider:
        if self._fallback is None:
            if self._fallback_factory is None:
                raise AIError("备用 AI 提供方未配置")
            try:
                self._fallback = self._fallback_factory()
            except (ValueError, OSError) as exc:
                raise AIError("备用 AI 提供方初始化失败，请检查接口与密钥配置") from exc
        return self._fallback

    def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            result = self.primary.chat(messages)
        except AILimitExceeded:
            raise
        except AIError as primary_error:
            self.last_fallback_reason = str(primary_error)
            try:
                result = self.fallback.chat(messages)
            except AILimitExceeded:
                raise
            except AIError as fallback_error:
                raise AIError(
                    f"GPT 失败: {primary_error}; 兼容接口失败: {fallback_error}"
                ) from fallback_error
            self.last_provider = self.fallback_name
            return result
        self.last_provider = self.primary_name
        self.last_fallback_reason = ""
        return result

    def close(self) -> None:
        try:
            self.primary.close()
        finally:
            if self._fallback is not None:
                self._fallback.close()
