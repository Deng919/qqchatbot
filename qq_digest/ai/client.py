from __future__ import annotations

import json
import time
from typing import Any, Callable
from uuid import uuid4

import httpx


class AIError(RuntimeError):
    pass


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.json_mode = json_mode
        self.max_retries = max(1, max_retries)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self._sleep = sleep_fn
        self.session_id = session_id or str(uuid4())
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

    def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8192,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        for attempt in range(self.max_retries):
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 < self.max_retries:
                    self._sleep(self.retry_base_seconds * (2**attempt))
                    continue
                raise AIError(f"AI 调用失败: {exc}") from exc

            retryable = response.status_code in {408, 429} or response.status_code >= 500
            if response.is_error:
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
                content = message.get("content", "") or message.get(
                    "reasoning_content", ""
                )
            except (ValueError, KeyError, IndexError) as exc:
                raise AIError(f"AI 返回结构无效: {exc}") from exc
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return self._extract_json(content)
        raise AIError("AI 调用失败: 已耗尽重试次数")

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
        raise AIError(f"AI 返回的不是有效 JSON: {text[:200]}")

    def close(self) -> None:
        self._client.close()
