import json

import httpx
import pytest

from qq_digest.ai.client import AIClient, AIError


def test_chat_sends_openai_compatible_request(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]},
        )
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
    )
    response = client.chat([{"role": "user", "content": "hi"}])

    assert response == {"ok": True}
    request = respx_mock.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"


def test_chat_raises_ai_error_on_http_failure(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server error")
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
    )

    with pytest.raises(AIError, match="500"):
        client.chat([{"role": "user", "content": "hi"}])
