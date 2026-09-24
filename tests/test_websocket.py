import asyncio
import json

from qq_digest.notify.websocket import WebSocketListener


class FakeConfig:
    api_base = "https://example.invalid"


class FakeNotifier:
    config = FakeConfig()

    def _get_access_token(self):
        return "test-token"

    def send_private_message(self, openid, message):
        return None


class FakeHandler:
    def handle_message(self, openid, content):
        raise AssertionError("本测试不应收到私信事件")


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


def test_websocket_identifies_and_sends_heartbeat_with_latest_sequence():
    async def scenario():
        listener = WebSocketListener(
            notifier=FakeNotifier(), handler=FakeHandler(), reconnect_delay=0
        )
        websocket = FakeWebSocket()
        listener._running = True

        await listener._handle_event(
            websocket, {"op": 10, "d": {"heartbeat_interval": 10}}
        )
        await listener._handle_event(websocket, {"op": 0, "s": 42, "t": "READY", "d": {}})
        await asyncio.sleep(0.025)
        await listener.stop()

        assert websocket.sent[0]["op"] == 2
        heartbeats = [payload for payload in websocket.sent if payload["op"] == 1]
        assert heartbeats
        assert heartbeats[-1]["d"] == 42
        assert listener._heartbeat_task is None

    asyncio.run(scenario())
