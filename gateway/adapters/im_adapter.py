"""
IM Bot 适配器
可插拔的 IM 接入层，支持企业微信/飞书/钉钉/Slack

每个适配器负责：
1. 接收 IM 的 webhook 消息
2. 转换为统一的 ChatRequest
3. 将 Agent 回复转发回 IM
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

from agent_core.schemas import ChatRequest, ChatResponse, UserRole

logger = structlog.get_logger()


class IMAdapter(ABC):
    """IM 适配器基类"""

    @abstractmethod
    async def parse_message(self, raw_payload: dict) -> ChatRequest | None:
        """将 IM webhook 消息解析为 ChatRequest"""
        ...

    @abstractmethod
    async def send_reply(self, response: ChatResponse, raw_payload: dict) -> None:
        """将 Agent 回复发送回 IM"""
        ...

    @abstractmethod
    async def verify_webhook(self, raw_payload: dict) -> dict | None:
        """验证 webhook 合法性（如企业微信的 URL 验证）"""
        ...


class WeChatWorkAdapter(IMAdapter):
    """企业微信适配器示例"""

    def __init__(self, corp_id: str, agent_id: str, secret: str):
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.secret = secret
        self._access_token: str = ""

    async def parse_message(self, raw_payload: dict) -> ChatRequest | None:
        msg_type = raw_payload.get("MsgType")
        if msg_type != "text":
            return None

        return ChatRequest(
            message=raw_payload.get("Content", ""),
            session_id=raw_payload.get("FromUserName", ""),
            user_id=raw_payload.get("FromUserName", "anonymous"),
            user_role=UserRole.VIEWER,
        )

    async def send_reply(self, response: ChatResponse, raw_payload: dict) -> None:
        user_id = raw_payload.get("FromUserName", "")
        # 企业微信发送消息 API
        # POST https://qyapi.weixin.qq.com/cgi-bin/message/send
        payload = {
            "touser": user_id,
            "msgtype": "markdown",
            "agentid": self.agent_id,
            "markdown": {"content": response.message},
        }
        token = await self._get_access_token()
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                json=payload,
            )

    async def verify_webhook(self, raw_payload: dict) -> dict | None:
        # 企业微信 webhook URL 验证逻辑
        return {"status": "ok"}

    async def _get_access_token(self) -> str:
        if not self._access_token:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={"corpid": self.corp_id, "corpsecret": self.secret},
                )
                data = resp.json()
                self._access_token = data.get("access_token", "")
        return self._access_token


class FeishuAdapter(IMAdapter):
    """飞书适配器骨架"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    async def parse_message(self, raw_payload: dict) -> ChatRequest | None:
        # 飞书消息解析
        event = raw_payload.get("event", {})
        message = event.get("message", {})
        content = message.get("content", "{}")

        import json
        try:
            content_data = json.loads(content)
            text = content_data.get("text", "")
        except json.JSONDecodeError:
            text = content

        sender = event.get("sender", {}).get("sender_id", {}).get("user_id", "anonymous")

        return ChatRequest(
            message=text,
            session_id=message.get("chat_id", ""),
            user_id=sender,
            user_role=UserRole.VIEWER,
        )

    async def send_reply(self, response: ChatResponse, raw_payload: dict) -> None:
        # TODO: 飞书发送消息 API
        pass

    async def verify_webhook(self, raw_payload: dict) -> dict | None:
        challenge = raw_payload.get("challenge")
        if challenge:
            return {"challenge": challenge}
        return None


class DingTalkAdapter(IMAdapter):
    """钉钉适配器骨架"""

    async def parse_message(self, raw_payload: dict) -> ChatRequest | None:
        text = raw_payload.get("text", {}).get("content", "").strip()
        sender = raw_payload.get("senderNick", "anonymous")

        return ChatRequest(
            message=text,
            session_id=raw_payload.get("conversationId", ""),
            user_id=sender,
            user_role=UserRole.VIEWER,
        )

    async def send_reply(self, response: ChatResponse, raw_payload: dict) -> None:
        # 钉钉通过 webhook 回复
        session_webhook = raw_payload.get("sessionWebhook", "")
        if session_webhook:
            async with httpx.AsyncClient() as client:
                await client.post(session_webhook, json={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": "OpsAgent",
                        "text": response.message,
                    }
                })

    async def verify_webhook(self, raw_payload: dict) -> dict | None:
        return None


class SlackAdapter(IMAdapter):
    """Slack 适配器骨架"""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token

    async def parse_message(self, raw_payload: dict) -> ChatRequest | None:
        event = raw_payload.get("event", {})
        if event.get("type") != "message" or event.get("bot_id"):
            return None

        return ChatRequest(
            message=event.get("text", ""),
            session_id=event.get("channel", ""),
            user_id=event.get("user", "anonymous"),
            user_role=UserRole.VIEWER,
        )

    async def send_reply(self, response: ChatResponse, raw_payload: dict) -> None:
        channel = raw_payload.get("event", {}).get("channel", "")
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel": channel, "text": response.message},
            )

    async def verify_webhook(self, raw_payload: dict) -> dict | None:
        challenge = raw_payload.get("challenge")
        if challenge:
            return {"challenge": challenge}
        return None
