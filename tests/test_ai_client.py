import json
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from qq_digest.ai.client import AIClient, AIError
from qq_digest.ai.bridge import BridgeAIClient, FallbackAIClient
from qq_digest.ai.factory import build_ai_client
from qq_digest.config import load_config
import yaml


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


def test_bridge_client_writes_request_invokes_wrapper_and_cleans_file(tmp_path):
    captured = {}

    def fake_runner(command, **kwargs):
        request_path = Path(command[command.index("-RequestPath") + 1])
        captured["request_path"] = request_path
        captured["request"] = json.loads(request_path.read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "content": json.dumps({"summary": "done"}),
                    "provider": "chatgpt_bridge",
                }
            ),
            stderr="",
        )

    client = BridgeAIClient(
        wrapper_path=tmp_path / "chat-current-account.ps1",
        account_directory=tmp_path / "accounts",
        model="gpt-test",
        timeout_seconds=30,
        runner=fake_runner,
        request_directory=tmp_path,
    )
    response = client.chat(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Summarize."},
        ]
    )

    assert response == {"summary": "done"}
    assert captured["request"]["model"] == "gpt-test"
    assert captured["request"]["messages"][1]["content"] == "Summarize."
    assert not captured["request_path"].exists()


def test_bridge_client_redacts_wrapper_stderr(tmp_path):
    def fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=json.dumps({"ok": False, "error": "Cockpit chat failed"}),
        )

    client = BridgeAIClient(
        wrapper_path=tmp_path / "chat-current-account.ps1",
        account_directory=tmp_path / "accounts",
        model="gpt-test",
        runner=fake_runner,
        request_directory=tmp_path,
    )

    with pytest.raises(AIError, match="Cockpit chat failed"):
        client.chat([{"role": "user", "content": "hi"}])


class _FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0
        self.closed = False

    def chat(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


def test_fallback_client_uses_gpt_without_calling_compatible_provider():
    primary = _FakeProvider(result={"provider": "gpt"})
    fallback = _FakeProvider(result={"provider": "compatible"})
    client = FallbackAIClient(primary=primary, fallback=fallback)

    assert client.chat([{"role": "user", "content": "hi"}]) == {
        "provider": "gpt"
    }
    assert primary.calls == 1
    assert fallback.calls == 0
    assert client.last_provider == "chatgpt_bridge"


def test_fallback_client_uses_compatible_provider_after_bridge_error():
    primary = _FakeProvider(error=AIError("bridge unavailable"))
    fallback = _FakeProvider(result={"provider": "compatible"})
    client = FallbackAIClient(primary=primary, fallback=fallback)

    assert client.chat([{"role": "user", "content": "hi"}]) == {
        "provider": "compatible"
    }
    assert primary.calls == 1
    assert fallback.calls == 1
    assert client.last_provider == "compatible"
    assert client.last_fallback_reason == "bridge unavailable"


def test_fallback_client_reports_both_provider_failures():
    primary = _FakeProvider(error=AIError("bridge unavailable"))
    fallback = _FakeProvider(error=AIError("compatible unavailable"))
    client = FallbackAIClient(primary=primary, fallback=fallback)

    with pytest.raises(AIError, match="bridge unavailable.*compatible unavailable"):
        client.chat([{"role": "user", "content": "hi"}])

    client.close()
    assert primary.closed is True
    assert fallback.closed is True


def test_build_ai_client_uses_configured_gpt_then_compatible_priority(
    tmp_path, monkeypatch
):
    wrapper = tmp_path / "chat-current-account.ps1"
    wrapper.write_text("", encoding="utf-8")
    accounts = tmp_path / "accounts"
    accounts.mkdir()
    raw = {
        "security": {"web_password_hash": "x" * 32},
        "groups": [],
        "ai": {
            "base_url": "https://configured.example/v1",
            "model": "compatible-model",
            "api_key_env": "TEST_AI_KEY",
            "provider_priority": ["chatgpt_bridge", "compatible"],
            "bridge_wrapper_path": str(wrapper),
            "bridge_account_directory": str(accounts),
            "bridge_model": "gpt-test",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("TEST_AI_KEY", "secret")

    client = build_ai_client(load_config(path))

    assert isinstance(client, FallbackAIClient)
    assert isinstance(client.primary, BridgeAIClient)
    assert isinstance(client.fallback, AIClient)
    assert client.primary.model == "gpt-test"
    assert client.fallback.base_url == "https://configured.example/v1"
