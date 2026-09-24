"""QQ Bot WebSocket 监听器。

连接 QQ 开放平台 WebSocket，接收私信事件，转发给指令处理器。
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging

import httpx
import websockets

from .notifier import QQBotNotifier
from .commands import BotCommandHandler

logger = logging.getLogger(__name__)


class WebSocketListener:
    """QQ Bot WebSocket 监听器。"""

    def __init__(
        self,
        *,
        notifier: QQBotNotifier,
        handler: BotCommandHandler,
        reconnect_delay: float = 5.0,
    ):
        self.notifier = notifier
        self.handler = handler
        self.reconnect_delay = reconnect_delay
        self._running = False
        self._sequence: int | None = None
        self._heartbeat_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 WebSocket 监听，断开后自动重连。"""
        self._running = True
        while self._running:
            try:
                await self._listen_loop()
            except Exception as exc:
                logger.error("WebSocket 连接异常: %s", exc)
            if self._running:
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        self._running = False
        await self._cancel_heartbeat()

    async def _cancel_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _listen_loop(self) -> None:
        """获取 WebSocket 地址并监听。"""
        token = await asyncio.to_thread(self.notifier._get_access_token)
        url = f"{self.notifier.config.api_base}/gateway"
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        ws_url = data.get("url")
        if not ws_url:
            raise RuntimeError(f"获取 WebSocket 地址失败: {data}")

        try:
            async with websockets.connect(ws_url) as ws:
                logger.info("QQ Bot WebSocket 已连接")
                async for raw in ws:
                    event = json.loads(raw)
                    await self._handle_event(ws, event)
        finally:
            await self._cancel_heartbeat()

    async def _heartbeat_loop(self, ws, interval_ms: int) -> None:
        interval = max(0.01, interval_ms / 1000)
        while self._running:
            await asyncio.sleep(interval)
            await ws.send(json.dumps({"op": 1, "d": self._sequence}))

    async def _handle_event(self, ws, event: dict) -> None:
        """处理 WebSocket 事件。"""
        op = event.get("op")
        s = event.get("s")
        if s is not None:
            self._sequence = s

        if op == 10:
            d = event.get("d", {})
            heartbeat_interval = d.get("heartbeat_interval", 30000)
            token = await asyncio.to_thread(self.notifier._get_access_token)
            identify = {
                "op": 2,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": 1 << 25,  # PRIVATE_MESSAGE
                },
            }
            await ws.send(json.dumps(identify))
            await self._cancel_heartbeat()
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(ws, heartbeat_interval)
            )
        elif op == 0:
            t = event.get("t")
            d = event.get("d", {})
            if t == "C2C_MESSAGE_CREATE":
                author = d.get("author", {})
                openid = author.get("user_openid", "")
                content = d.get("content", "").strip()
                if openid and content:
                    result = self.handler.handle_message(openid, content)
                    await asyncio.to_thread(
                        self.notifier.send_private_message, openid, result.message
                    )
