"""官方 QQ Bot 通知模块。

通过 QQ 开放平台 API 发送每日摘要私信，记录投递状态。
设计文档要求：AppID/AppSecret 获取 token、私信发送、WebSocket 指令监听、白名单 OpenID 鉴权。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass(frozen=True)
class BotConfig:
    """QQ Bot 配置（从 config.yaml 的 qq_bot 段加载）。"""
    app_id: str
    app_secret: str
    allowed_openids: list[str] = field(default_factory=list)
    api_base: str = "https://api.sgroup.qq.com"
    websocket_url: str = "wss://api.sgroup.qq.com/websockets"
    max_retries: int = 3
    retry_base_seconds: int = 60


@dataclass(frozen=True)
class SendResult:
    success: bool
    error: str = ""
    raw_response: dict = field(default_factory=dict)


class QQBotNotifier:
    """官方 QQ Bot 消息投递器。"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._token: str = ""
        self._token_expires: float = 0.0

    def _get_access_token(self) -> str:
        """获取或刷新 access token。"""
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        url = f"{self.config.api_base}/cgi-bin/token"
        params = {
            "grant_type": "client_credentials",
            "appid": self.config.app_id,
            "secret": self.config.app_secret,
        }
        resp = httpx.post(url, params=params, timeout=30)
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"获取 access token 失败: {data}")
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _headers(self) -> dict:
        token = self._get_access_token()
        return {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }

    def send_private_message(self, openid: str, content: str) -> SendResult:
        """发送私信给指定用户 OpenID。

        QQ Bot 主动消息有频率限制，每次发送后记录到 send_log。
        """
        url = f"{self.config.api_base}/v2/users/{openid}/messages"
        payload = {
            "content": content,
            "msg_type": 0,  # 0=文本
        }
        try:
            resp = httpx.post(url, json=payload, headers=self._headers(), timeout=30)
            data = resp.json()
            if resp.status_code == 200 and "id" in data:
                return SendResult(success=True, raw_response=data)
            return SendResult(success=False, error=str(data), raw_response=data)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    def send_report_notification(
        self, openid: str, group_name: str, report_date: str,
        pending_candidates: int, report_url: str = "",
    ) -> SendResult:
        """发送每日摘要通知。"""
        content = (
            f"📋 {report_date} 群「{group_name}」日报已生成\n"
            f"待审核重点信息：{pending_candidates} 条\n"
        )
        if report_url:
            content += f"查看报告：{report_url}\n"
        if pending_candidates > 0:
            content += "请前往 Web 审核界面或回复指令审核\n"
        return self.send_private_message(openid, content)

    def is_authorized(self, openid: str) -> bool:
        """检查 OpenID 是否在白名单中。"""
        return openid in self.config.allowed_openids
