"""
审计日志模块
记录所有 Agent 操作：谁、什么时候、做了什么、结果如何
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import structlog

from agent_core.schemas import AgentRoute, AuditEntry, IntentType, RiskLevel

logger = structlog.get_logger()


class AuditLogger:
    """审计日志记录器 - Phase 1 先写文件/stdout，Phase 3 接入持久化存储"""

    def __init__(self):
        self._entries: list[AuditEntry] = []

    def log(
        self,
        user_id: str,
        session_id: str,
        intent: Optional[IntentType] = None,
        route: Optional[AgentRoute] = None,
        risk_level: Optional[RiskLevel] = None,
        needs_approval: bool = False,
        tool_name: Optional[str] = None,
        action: Optional[str] = None,
        tool_calls: list[str] | None = None,
        params: dict[str, Any] | None = None,
        result_summary: str = "",
        success: bool = True,
        duration_ms: int = 0,
    ) -> AuditEntry:
        entry = AuditEntry(
            user_id=user_id,
            session_id=session_id,
            intent=intent,
            route=route,
            risk_level=risk_level,
            needs_approval=needs_approval,
            tool_name=tool_name,
            action=action,
            tool_calls=tool_calls or [],
            params=self._sanitize_params(params or {}),
            result_summary=result_summary,
            success=success,
            duration_ms=duration_ms,
        )
        self._entries.append(entry)

        # 结构化日志输出
        logger.info(
            "audit_log",
            user=user_id,
            intent=intent.value if intent else None,
            route=route.value if route else None,
            risk_level=risk_level.value if risk_level else None,
            needs_approval=needs_approval,
            tool=tool_name,
            action=action,
            tool_calls=tool_calls or [],
            success=success,
            duration_ms=duration_ms,
        )
        return entry

    def _sanitize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """脱敏处理：移除密码、Token 等敏感信息"""
        sensitive_keys = {"password", "token", "secret", "api_key", "credential", "connection_string"}
        sanitized = {}
        for k, v in params.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, str) and len(v) > 500:
                sanitized[k] = v[:500] + "...(truncated)"
            else:
                sanitized[k] = v
        return sanitized

    def get_recent(self, limit: int = 50) -> list[AuditEntry]:
        return self._entries[-limit:]

    def get_by_user(self, user_id: str, limit: int = 50) -> list[AuditEntry]:
        return [e for e in self._entries if e.user_id == user_id][-limit:]


# 全局单例
audit_logger = AuditLogger()
