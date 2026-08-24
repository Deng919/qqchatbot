from __future__ import annotations

import json
from typing import Any

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
        timeout_seconds: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            response = self._client.post(
                "/chat/completions",
                json={"model": self.model, "messages": messages, "temperature": 0.2},
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise AIError(f"AI 调用失败: {exc}") from exc

    def close(self) -> None:
        self._client.close()
