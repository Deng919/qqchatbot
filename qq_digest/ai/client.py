from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable
from uuid import uuid4

import httpx


class AIError(RuntimeError):
    pass


class AILimitExceeded(AIError):
    pass


class _AccountedResponse(dict):
    """Keep accounting out of JSON keys and bind rejection to this exact attempt."""
    def __init__(self,payload,reject):
        super().__init__(payload)
        self._reject=reject


def reject_response(response):
    if isinstance(response,_AccountedResponse):
        response._reject()


class AIClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        json_mode: bool = False,
        timeout_seconds: float = 120,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        retry_base_seconds: float = 2.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        session_id: str | None = None,
        usage_service=None,
        purpose: str = 'summary',
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.json_mode = json_mode
        self.max_retries = max(1, max_retries)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self._sleep = sleep_fn
        self.session_id = session_id or str(uuid4())
        self.usage_service = usage_service
        self.purpose = purpose
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "qq-digest/0.1",
                "x-opencode-session": self.session_id,
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8192,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        for attempt in range(self.max_retries):
            attempt_id = self._reserve_usage()
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.RequestError as exc:
                self._finish_usage(attempt_id, 'unknown', None)
                if attempt + 1 < self.max_retries:
                    self._sleep(self.retry_base_seconds * (2**attempt))
                    continue
                raise AIError(f"AI 调用失败: {exc}") from exc

            try:
                body = response.json()
            except ValueError:
                body = None
            usage = body.get('usage') if isinstance(body, dict) else None
            retryable = response.status_code in {408, 429} or response.status_code >= 500
            if response.is_error:
                self._finish_usage(attempt_id, 'failed', usage)
                if retryable and attempt + 1 < self.max_retries:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = self.retry_base_seconds * (2**attempt)
                    self._sleep(max(0.0, delay))
                    continue
                provider_message = self._provider_error_message(response)
                if provider_message:
                    raise AIError(
                        f"AI 调用失败 ({response.status_code}): {provider_message}"
                    )
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise AIError(f"AI 调用失败: {exc}") from exc

            try:
                message = response.json()["choices"][0]["message"]
                content = message.get("content")
                if content is None or content == '':
                    content = message.get("reasoning_content")
                if content is None:
                    content = ''
                if not isinstance(content, str):
                    raise ValueError('AI 内容必须是文本')
            except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                self._finish_usage(attempt_id, 'failed', usage)
                raise AIError(f"AI 返回结构无效: {exc}") from exc
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                try:
                    parsed = self._extract_json(content)
                except AIError as exc:
                    self._finish_usage(attempt_id, 'failed', usage)
                    if attempt + 1 < self.max_retries:
                        self._sleep(self.retry_base_seconds * (2**attempt))
                        continue
                    raise AIError("AI 连续返回无效 JSON，已耗尽重试次数") from exc
            if isinstance(parsed, dict):
                self._finish_usage(attempt_id, 'success', usage)
                return _AccountedResponse(parsed,lambda:self._reject_usage(attempt_id)) if attempt_id is not None else parsed
            self._finish_usage(attempt_id, 'failed', usage)
            if attempt + 1 < self.max_retries:
                self._sleep(self.retry_base_seconds * (2**attempt))
                continue
            raise AIError("AI 连续返回非对象 JSON，已耗尽重试次数")
        raise AIError("AI 调用失败: 已耗尽重试次数")

    def _reserve_usage(self):
        if self.usage_service is None:
            return None
        from ..ai_usage import UsageLimitError
        try:
            return self.usage_service.reserve(self.base_url, self.model, self.purpose, self.timeout_seconds)
        except UsageLimitError as exc:
            raise AILimitExceeded(str(exc)) from exc
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise AIError('无法检查 AI 用量，请检查存储后重试') from exc

    def _finish_usage(self, attempt_id, status, usage):
        if attempt_id is None:
            return
        try:
            self.usage_service.finish(attempt_id, status, usage)
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise AIError('无法记录 AI 用量，请检查存储后重试') from exc

    def _reject_usage(self,attempt_id):
        try:
            self.usage_service.reject(attempt_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise AIError('无法记录 AI 用量，请检查存储后重试') from exc

    @staticmethod
    def _provider_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message", "")
        elif isinstance(error, str):
            message = error
        else:
            message = payload.get("message", "")
        if not isinstance(message, str):
            return ""
        return " ".join(message.split())[:500]

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Try to extract JSON from a response that may have markdown fences or extra text."""
        text = text.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            start = 1
            end = len(lines)
            for i, line in enumerate(lines[1:], 1):
                if line.strip().startswith("```"):
                    end = i
                    break
            text = "\n".join(lines[start:end])
        # Find first { and last }
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass
        raise AIError("AI 返回的不是有效 JSON")

    def close(self) -> None:
        self._client.close()
