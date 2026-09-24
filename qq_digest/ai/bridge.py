from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol

from .client import AIError


class ChatProvider(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]: ...

    def close(self) -> None: ...


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
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
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
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
            )
            if result.returncode != 0:
                message = "chatgpt-bridge 调用失败"
                try:
                    error = json.loads(result.stderr.strip())
                    if isinstance(error, dict) and isinstance(error.get("error"), str):
                        message = error["error"][:1000]
                except (json.JSONDecodeError, AttributeError):
                    pass
                raise AIError(message)
            try:
                payload = json.loads(result.stdout)
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
        except AIError as primary_error:
            self.last_fallback_reason = str(primary_error)
            try:
                result = self.fallback.chat(messages)
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
