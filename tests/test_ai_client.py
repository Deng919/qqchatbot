import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from qq_digest.ai.client import AIClient, AIError
from qq_digest.ai.factory import build_ai_client


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
        max_retries=1,
    )
    response = client.chat([{"role": "user", "content": "hi"}])

    assert response == {"ok": True}
    request = respx_mock.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"


def test_chat_requests_json_output_when_enabled(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )
    )
    client = AIClient(
        base_url="https://api.deepseek.com",
        api_key="synthetic-key",
        model="deepseek-flash",
        json_mode=True,
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=1,
    )

    assert client.chat([{"role": "system", "content": "Return JSON"}]) == {"ok": True}
    payload = json.loads(respx_mock.calls[0].request.content)
    assert payload["response_format"] == {"type": "json_object"}


def test_factory_passes_json_mode_to_compatible_client(tmp_path):
    config = SimpleNamespace(
        archive_path=tmp_path/'archive.sqlite',
        summary=SimpleNamespace(timezone='Asia/Shanghai'),
        ai=SimpleNamespace(
            provider_priority=["compatible"],
            model="deepseek-flash",
            json_mode=True,
            timeout_seconds=5,
            max_retries=1,
            retry_base_seconds=0,
        ),
        resolve_base_url=lambda: "https://api.deepseek.com",
        resolve_api_key=lambda: "synthetic-key",
    )

    client = build_ai_client(config)

    assert client.json_mode is True
    client.close()


def test_chat_sends_stable_opencode_session_headers(respx_mock):
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
        session_id="test-session",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=1,
    )

    client.chat([{"role": "user", "content": "first"}])
    client.chat([{"role": "user", "content": "second"}])

    assert len(respx_mock.calls) == 2
    for call in respx_mock.calls:
        assert call.request.headers["user-agent"] == "qq-digest/0.1"
        assert call.request.headers["x-opencode-session"] == "test-session"


def test_default_opencode_sessions_are_stable_unique_uuid4_values(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]},
        )
    )
    first = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=1,
    )
    second = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=1,
    )

    first_session = UUID(first.session_id)
    second_session = UUID(second.session_id)
    assert first_session.version == 4
    assert second_session.version == 4
    assert str(first_session) == first.session_id
    assert str(second_session) == second.session_id
    assert first.session_id != second.session_id

    first.chat([{"role": "user", "content": "first"}])
    first.chat([{"role": "user", "content": "second"}])
    assert [
        call.request.headers["x-opencode-session"] for call in respx_mock.calls
    ] == [first.session_id, first.session_id]


def test_chat_raises_ai_error_on_http_failure(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server error")
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=1,
    )

    with pytest.raises(AIError, match="500"):
        client.chat([{"role": "user", "content": "hi"}])


def test_chat_retries_429_and_honors_retry_after(respx_mock):
    route = respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}, text="limited"),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]},
            ),
        ]
    )
    delays = []
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=3,
        retry_base_seconds=1,
        sleep_fn=delays.append,
    )

    assert client.chat([{"role": "user", "content": "hi"}]) == {"ok": True}
    assert route.call_count == 2
    assert delays == [3.0]


def test_chat_does_not_retry_permanent_client_error(respx_mock):
    route = respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(400, text="bad request")
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=3,
        retry_base_seconds=0,
    )

    with pytest.raises(AIError, match="400"):
        client.chat([{"role": "user", "content": "hi"}])

    assert route.call_count == 1


def test_chat_retries_malformed_json_without_leaking_response(respx_mock):
    private_fragment = "private message excerpt"
    route = respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"text":"' + private_fragment}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]}),
        ]
    )
    delays = []
    client = AIClient(
        base_url="https://api.example.com/v1", api_key="synthetic-key",
        model="test-model", transport=httpx.MockTransport(respx_mock.handler),
        max_retries=2, retry_base_seconds=0, sleep_fn=delays.append,
    )

    assert client.chat([{"role": "user", "content": "test"}]) == {"ok": True}
    assert route.call_count == 2


def test_chat_malformed_json_error_does_not_include_private_output(respx_mock):
    private_fragment = "private message excerpt"
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": private_fragment}}]}
        )
    )
    client = AIClient(
        base_url="https://api.example.com/v1", api_key="synthetic-key",
        model="test-model", transport=httpx.MockTransport(respx_mock.handler),
        max_retries=2, retry_base_seconds=0,
    )

    with pytest.raises(AIError) as captured:
        client.chat([{"role": "user", "content": "test"}])
    assert private_fragment not in str(captured.value)


def test_chat_includes_safe_provider_error_message(respx_mock):
    respx_mock.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            401,
            json={"error": {"message": "Model deepseek-v4.1-flash is not supported"}},
        )
    )
    client = AIClient(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="deepseek-v4.1-flash",
        transport=httpx.MockTransport(respx_mock.handler),
        max_retries=1,
    )

    with pytest.raises(
        AIError, match="Model deepseek-v4.1-flash is not supported"
    ):
        client.chat([{"role": "user", "content": "hi"}])
